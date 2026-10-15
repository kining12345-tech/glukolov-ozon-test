import asyncio
import json
import os
import threading
import time
import traceback
import uuid

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from playwright.async_api import async_playwright


PORT = int(os.environ.get("PORT", "8000"))

INSTANCE_ID = str(uuid.uuid4())[:8]

state_lock = threading.Lock()

state = {
    "instance_id": INSTANCE_ID,
    "running": False,
    "started_at": None,
    "finished_at": None,
    "progress": "IDLE",
    "result": None,
    "last_error": None,
}


def log(message):
    print(
        time.strftime("%Y-%m-%d %H:%M:%S")
        + " "
        + str(message),
        flush=True,
    )


def get_proxy():
    proxy_url = os.getenv("PROXY_URL")

    if proxy_url:
        return {"server": proxy_url}

    host = os.getenv("PROXY_HOST")
    port = os.getenv("PROXY_PORT")
    user = os.getenv("PROXY_USER")
    password = os.getenv("PROXY_PASS")

    if not host or not port:
        return None

    proxy = {
        "server": f"http://{host}:{port}",
    }

    if user:
        proxy["username"] = user

    if password:
        proxy["password"] = password

    return proxy


def snapshot():
    with state_lock:
        return json.loads(json.dumps(state, ensure_ascii=False))


def update_state(**kwargs):
    with state_lock:
        state.update(kwargs)


def update_result(browser_name, test_name, data):
    with state_lock:
        if state["result"] is None:
            state["result"] = {
                "instance_id": INSTANCE_ID,
                "browsers": {},
            }

        browsers = state["result"]["browsers"]

        if browser_name not in browsers:
            browsers[browser_name] = {
                "tests": {},
                "steps": [],
            }

        browser = browsers[browser_name]

        if test_name == "_step":
            browser["steps"].append(data)
        elif test_name == "_error":
            browser["error"] = data
        else:
            browser["tests"][test_name] = data

        browser["updated_at"] = time.strftime(
            "%Y-%m-%d %H:%M:%S"
        )


async def safe_value(awaitable, timeout=3):
    try:
        return await asyncio.wait_for(
            awaitable,
            timeout=timeout,
        )
    except Exception as exc:
        return {
            "error": f"{type(exc).__name__}: {str(exc)[:300]}"
        }


async def run_browser_test(playwright, browser_name):
    proxy = get_proxy()

    update_state(progress=f"{browser_name}: LAUNCHING")
    log(f"{browser_name}: LAUNCHING")

    update_result(
        browser_name,
        "_step",
        "LAUNCHING",
    )

    browser = None
    context = None

    browser_result = {
        "browser": browser_name,
        "instance_id": INSTANCE_ID,
        "started_at": time.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "proxy_configured": bool(proxy),
        "tests": {},
    }

    try:
        if browser_name == "chromium":
            browser = await asyncio.wait_for(
                playwright.chromium.launch(
                    headless=True,
                    channel="chromium",
                    proxy=proxy,
                    timeout=20000,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                ),
                timeout=25,
            )

            user_agent = (
                "Mozilla/5.0 (Linux; Android 13; Mobile) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/141.0.0.0 Mobile Safari/537.36"
            )

        elif browser_name == "firefox":
            browser = await asyncio.wait_for(
                playwright.firefox.launch(
                    headless=True,
                    proxy=proxy,
                    timeout=20000,
                ),
                timeout=25,
            )

            user_agent = (
                "Mozilla/5.0 (Android 13; Mobile; rv:128.0) "
                "Gecko/128.0 Firefox/128.0"
            )

        else:
            raise ValueError("Unknown browser")

        update_state(
            progress=f"{browser_name}: BROWSER_STARTED"
        )

        update_result(
            browser_name,
            "_step",
            "BROWSER_STARTED",
        )

        log(f"{browser_name}: BROWSER_STARTED")

        context = await asyncio.wait_for(
            browser.new_context(
                user_agent=user_agent,
                locale="ru-RU",
                viewport={
                    "width": 390,
                    "height": 844,
                },
            ),
            timeout=15,
        )

        page = await asyncio.wait_for(
            context.new_page(),
            timeout=10,
        )

        page.set_default_navigation_timeout(15000)
        page.set_default_timeout(5000)

        checks = [
            (
                "ipify",
                "https://api.ipify.org/?format=json",
            ),
            (
                "httpbin",
                "https://httpbin.org/get",
            ),
            (
                "ozon",
                "https://www.ozon.ru/",
            ),
        ]

        for test_name, url in checks:
            update_state(
                progress=f"{browser_name}: {test_name.upper()}_START"
            )

            log(
                f"{browser_name}: "
                f"{test_name.upper()}_START"
            )

            item = {
                "url": url,
                "started_at": time.strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }

            try:
                response = await asyncio.wait_for(
                    page.goto(
                        url,
                        wait_until="commit",
                        timeout=15000,
                    ),
                    timeout=18,
                )

                item["status"] = (
                    response.status if response else None
                )

                item["final_url"] = page.url

                title = await safe_value(
                    page.title(),
                    timeout=3,
                )

                item["title"] = title

                html = await safe_value(
                    page.content(),
                    timeout=3,
                )

                if isinstance(html, str):
                    item["html_length"] = len(html)

                    lowered = html.lower()

                    item["markers"] = {
                        "challenge": (
                            "challenge" in lowered
                        ),
                        "antibot": (
                            "antibot" in lowered
                        ),
                        "captcha": (
                            "captcha" in lowered
                        ),
                        "tilegrid": (
                            "tilegrid" in lowered
                        ),
                        "pricev2": (
                            "priceV2" in html
                        ),
                        "product": (
                            "product" in lowered
                        ),
                    }
                else:
                    item["html_error"] = html

                if test_name == "ipify":
                    body = await safe_value(
                        page.locator("body").inner_text(),
                        timeout=3,
                    )

                    if isinstance(body, str):
                        item["body_text"] = body[:500]
                    else:
                        item["body_error"] = body

                if test_name == "ozon":
                    body = await safe_value(
                        page.locator("body").inner_text(),
                        timeout=3,
                    )

                    if isinstance(body, str):
                        item["page_text"] = body[:1500]
                    else:
                        item["page_text_error"] = body

            except Exception as exc:
                item["error_type"] = type(exc).__name__
                item["error"] = str(exc)[:1000]
                item["current_url"] = page.url

                log(
                    f"{browser_name} {test_name} ERROR: "
                    f"{type(exc).__name__}: {str(exc)[:500]}"
                )

            item["finished_at"] = time.strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            # Сохраняем результат сразу после каждой проверки.
            update_result(
                browser_name,
                test_name,
                item,
            )

            browser_result["tests"][test_name] = item

            log(
                f"{browser_name} {test_name} RESULT: "
                + json.dumps(
                    item,
                    ensure_ascii=False,
                )
            )

        browser_result["finished_at"] = time.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        update_result(
            browser_name,
            "_step",
            "ALL_URLS_CHECKED",
        )

        update_state(
            progress=f"{browser_name}: COMPLETED"
        )

    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "message": str(exc)[:1000],
            "traceback": traceback.format_exc()[-5000:],
        }

        browser_result["error"] = error

        update_result(
            browser_name,
            "_error",
            error,
        )

        update_state(
            progress=f"{browser_name}: FAILED"
        )

        log(
            f"{browser_name} FAILED: "
            + json.dumps(
                error,
                ensure_ascii=False,
            )
        )

    finally:
        if context is not None:
            try:
                await asyncio.wait_for(
                    context.close(),
                    timeout=5,
                )
            except Exception as exc:
                log(
                    f"{browser_name} context close: "
                    f"{type(exc).__name__}"
                )

        if browser is not None:
            try:
                await asyncio.wait_for(
                    browser.close(),
                    timeout=5,
                )
            except Exception as exc:
                log(
                    f"{browser_name} browser close: "
                    f"{type(exc).__name__}"
                )

    return browser_result


async def run_tests(browser_names):
    combined = {
        "instance_id": INSTANCE_ID,
        "proxy_configured": bool(get_proxy()),
        "requested_browsers": browser_names,
        "started_at": time.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    }

    update_state(
        result={
            "instance_id": INSTANCE_ID,
            "browsers": {},
            "requested_browsers": browser_names,
        }
    )

    async with async_playwright() as playwright:
        for browser_name in browser_names:
            await run_browser_test(
                playwright,
                browser_name,
            )

    combined["finished_at"] = time.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    update_state(
        progress="COMPLETED",
        finished_at=combined["finished_at"],
    )

    log(
        "TEST COMPLETED: "
        + json.dumps(
            combined,
            ensure_ascii=False,
        )
    )


def worker(browser_names):
    update_state(
        running=True,
        started_at=time.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        finished_at=None,
        progress="STARTING",
        result={
            "instance_id": INSTANCE_ID,
            "browsers": {},
            "requested_browsers": browser_names,
        },
        last_error=None,
    )

    try:
        asyncio.run(run_tests(browser_names))

    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "message": str(exc)[:1000],
            "traceback": traceback.format_exc()[-5000:],
        }

        update_state(
            progress="FAILED",
            last_error=error,
            finished_at=time.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        )

        log(
            "TEST FAILED: "
            + json.dumps(
                error,
                ensure_ascii=False,
            )
        )

    finally:
        update_state(running=False)


class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, code=200):
        body = json.dumps(
            data,
            ensure_ascii=False,
        ).encode("utf-8")

        self.send_response(code)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )

        self.send_header(
            "Cache-Control",
            "no-store, no-cache, must-revalidate",
        )

        self.send_header(
            "Content-Length",
            str(len(body)),
        )

        self.end_headers()

        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/health":
            self.send_json({
                "ok": True,
                "project": "Glukolov browser proxy test v2",
                "port": PORT,
                "instance_id": INSTANCE_ID,
                "proxy_configured": bool(get_proxy()),
                "proxy_variables_present": {
                    name: bool(os.getenv(name))
                    for name in (
                        "PROXY_HOST",
                        "PROXY_PORT",
                        "PROXY_USER",
                        "PROXY_PASS",
                    )
                },
            })
            return

        if path == "/test":
            browser_param = params.get(
                "browser",
                ["firefox"],
            )[0].lower()

            if browser_param == "both":
                browser_names = [
                    "chromium",
                    "firefox",
                ]
            elif browser_param in (
                "chromium",
                "firefox",
            ):
                browser_names = [browser_param]
            else:
                self.send_json({
                    "error": "Invalid browser",
                    "allowed": [
                        "firefox",
                        "chromium",
                        "both",
                    ],
                }, 400)
                return

            with state_lock:
                already_running = state["running"]

                if not already_running:
                    state["running"] = True
                    state["progress"] = "QUEUED"

            if not already_running:
                thread = threading.Thread(
                    target=worker,
                    args=(browser_names,),
                    daemon=True,
                )
                thread.start()

            self.send_json({
                "started": not already_running,
                "already_running": already_running,
                "instance_id": INSTANCE_ID,
                "browsers": browser_names,
                "message": (
                    "Test started"
                    if not already_running
                    else "Test already running"
                ),
            })
            return

        if path == "/status":
            self.send_json(snapshot())
            return

        self.send_json({
            "project": "Glukolov browser proxy test v2",
            "instance_id": INSTANCE_ID,
            "endpoints": {
                "health": "/health",
                "test_firefox": "/test?browser=firefox",
                "test_chromium": "/test?browser=chromium",
                "test_both": "/test?browser=both",
                "status": "/status",
            },
        })


    def log_message(self, fmt, *args):
        log("HTTP " + (fmt % args))


log(
    "Glukolov browser comparison v2 listening "
    f"on port {PORT}; instance_id={INSTANCE_ID}"
)

server = ThreadingHTTPServer(
    ("0.0.0.0", PORT),
    Handler,
)

server.serve_forever()
