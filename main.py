import asyncio
import json
import os
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.async_api import async_playwright


PORT = int(os.environ.get("PORT", "8000"))

status = {
    "running": False,
    "result": None,
    "last_error": None,
}

status_lock = threading.Lock()


def log(message):
    print(message, flush=True)


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

    proxy = {"server": f"http://{host}:{port}"}

    if user:
        proxy["username"] = user

    if password:
        proxy["password"] = password

    return proxy


async def run_browser_test(playwright, browser_name):
    result = {
        "browser": browser_name,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "steps": [],
        "tests": {},
    }

    proxy = get_proxy()
    result["proxy_configured"] = bool(proxy)

    try:
        if browser_name == "chromium":
            browser = await playwright.chromium.launch(
                headless=True,
                channel="chromium",
                proxy=proxy,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )

            user_agent = (
                "Mozilla/5.0 (Linux; Android 13; Mobile) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/141.0.0.0 Mobile Safari/537.36"
            )

        elif browser_name == "firefox":
            browser = await playwright.firefox.launch(
                headless=True,
                proxy=proxy,
            )

            user_agent = (
                "Mozilla/5.0 (Android 13; Mobile; rv:128.0) "
                "Gecko/128.0 Firefox/128.0"
            )

        else:
            raise ValueError("Unsupported browser")

        result["steps"].append("BROWSER_STARTED")
        log(f"{browser_name}: BROWSER_STARTED")

        try:
            # Firefox не поддерживает is_mobile и has_touch
            # в Browser.new_context. Для сравнения используем
            # одинаковые размеры окна и локаль.
            context = await browser.new_context(
                user_agent=user_agent,
                locale="ru-RU",
                viewport={"width": 390, "height": 844},
            )

            page = await context.new_page()

            async def check(name, url):
                item = {"url": url}

                try:
                    response = await page.goto(
                        url,
                        wait_until="commit",
                        timeout=25000,
                    )

                    item["status"] = (
                        response.status if response else None
                    )
                    item["final_url"] = page.url
                    item["title"] = await page.title()

                    html = await page.content()
                    item["html_length"] = len(html)

                    item["markers"] = {
                        "challenge": "challenge" in html.lower(),
                        "antibot": "antibot" in html.lower(),
                        "captcha": "captcha" in html.lower(),
                        "tilegrid": "tilegrid" in html.lower(),
                        "pricev2": "priceV2" in html,
                        "product": "product" in html.lower(),
                    }

                    if name == "ipify":
                        item["body_text"] = (
                            await page.locator("body").inner_text()
                        )[:1000]

                    if name == "ozon":
                        # Сохраняем только видимый текст страницы,
                        # не записывая токены challenge из HTML.
                        try:
                            item["page_text"] = (
                                await page.locator("body").inner_text()
                            )[:2500]
                        except Exception:
                            pass

                except Exception as exc:
                    item["error"] = str(exc)
                    item["current_url"] = page.url

                result["tests"][name] = item
                log(
                    f"{browser_name} {name}: "
                    + json.dumps(item, ensure_ascii=False)
                )

            result["steps"].append(
                "PROXY_CONFIGURED" if proxy else "NO_PROXY"
            )

            await check(
                "ipify",
                "https://api.ipify.org/?format=json",
            )

            await check(
                "httpbin",
                "https://httpbin.org/get",
            )

            await check(
                "ozon",
                "https://www.ozon.ru/",
            )

            result["steps"].append("ALL_URLS_CHECKED")
            await context.close()

        finally:
            await browser.close()

    except Exception as exc:
        result["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        log(f"{browser_name} FAILED: {exc}")

    result["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return result


async def run_all_tests():
    combined = {
        "project": "Glukolov Chromium and Firefox proxy comparison",
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "proxy_configured": bool(get_proxy()),
        "browsers": {},
    }

    async with async_playwright() as playwright:
        combined["browsers"]["chromium"] = await run_browser_test(
            playwright,
            "chromium",
        )

        combined["browsers"]["firefox"] = await run_browser_test(
            playwright,
            "firefox",
        )

    combined["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return combined


def worker():
    with status_lock:
        if status["running"]:
            return

        status["running"] = True
        status["last_error"] = None

    try:
        result = asyncio.run(run_all_tests())

        with status_lock:
            status["result"] = result

    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }

        log("TEST FAILED: " + json.dumps(error, ensure_ascii=False))

        with status_lock:
            status["last_error"] = error

    finally:
        with status_lock:
            status["running"] = False


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
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            self.send_json({
                "ok": True,
                "project": "Glukolov Chromium and Firefox proxy test",
                "port": PORT,
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

        if self.path.startswith("/test"):
            with status_lock:
                already_running = status["running"]

            if not already_running:
                threading.Thread(
                    target=worker,
                    daemon=True,
                ).start()

            self.send_json({
                "started": not already_running,
                "already_running": already_running,
            })
            return

        if self.path.startswith("/status"):
            with status_lock:
                snapshot = dict(status)

            self.send_json(snapshot)
            return

        self.send_json({
            "project": "Glukolov Chromium and Firefox proxy test",
            "endpoints": ["/health", "/test", "/status"],
        })


    def log_message(self, fmt, *args):
        log("HTTP " + (fmt % args))


log(f"Glukolov browser comparison listening on port {PORT}")

ThreadingHTTPServer(
    ("0.0.0.0", PORT),
    Handler,
).serve_forever()
