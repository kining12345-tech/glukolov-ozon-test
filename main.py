import asyncio
import json
import os
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

from playwright.async_api import async_playwright


PORT = int(os.environ.get("PORT", "8000"))

status = {
    "running": False,
    "result": None,
    "last_error": None
}

status_lock = threading.Lock()


def mark(text):
    print(text, flush=True)


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


async def chromium_test():
    result = {
        "project": "Glukolov Chromium Russian proxy test",
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "browser": "Chromium",
        "proxy_configured": False,
        "steps": [],
        "tests": {}
    }

    proxy = get_proxy()
    result["proxy_configured"] = bool(proxy)

    if not proxy:
        result["steps"].append("PROXY_NOT_CONFIGURED")

    async with async_playwright() as p:
        result["steps"].append("PLAYWRIGHT_READY")
        mark("PLAYWRIGHT READY")

        browser = await p.chromium.launch(
            headless=True,
            channel="chromium",
            proxy=proxy,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage"
            ]
        )

        result["steps"].append("CHROMIUM_STARTED")
        mark("CHROMIUM STARTED")

        try:
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Linux; Android 13; Mobile) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/141.0.0.0 Mobile Safari/537.36"
                ),
                locale="ru-RU",
                viewport={"width": 390, "height": 844}
            )

            page = await context.new_page()

            async def check(name, url):
                item = {"url": url}

                try:
                    response = await page.goto(
                        url,
                        wait_until="commit",
                        timeout=20000
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
                        "tilegrid": "tilegrid" in html.lower(),
                        "pricev2": "priceV2" in html,
                        "product": "product" in html.lower()
                    }

                    if name == "ipify":
                        try:
                            item["body_text"] = (
                                await page.locator("body").inner_text()
                            )[:1000]
                        except Exception:
                            pass

                except Exception as e:
                    item["error"] = str(e)
                    item["current_url"] = page.url

                result["tests"][name] = item
                mark(f"{name.upper()} DONE: {item}")

            result["steps"].append("PROXY_CONFIG_OK" if proxy else "NO_PROXY")

            await check(
                "ipify",
                "https://api.ipify.org/?format=json"
            )
            result["steps"].append("IP_DONE")

            await check(
                "httpbin",
                "https://httpbin.org/get"
            )
            result["steps"].append("HTTPBIN_DONE")

            await check(
                "ozon",
                "https://www.ozon.ru/"
            )
            result["steps"].append("OZON_DONE")

        finally:
            await browser.close()

    result["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return result


def worker():
    with status_lock:
        if status["running"]:
            return
        status["running"] = True
        status["last_error"] = None

    try:
        result = asyncio.run(chromium_test())

        with status_lock:
            status["result"] = result

    except Exception as e:
        error = {
            "type": type(e).__name__,
            "message": str(e),
            "traceback": traceback.format_exc()
        }

        mark("TEST FAILED: " + json.dumps(error, ensure_ascii=False))

        with status_lock:
            status["last_error"] = error

    finally:
        with status_lock:
            status["running"] = False


class Handler(BaseHTTPRequestHandler):
    def respond_json(self, data, code=200):
        body = json.dumps(
            data, ensure_ascii=False
        ).encode("utf-8")

        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            self.respond_json({
                "ok": True,
                "project": "Glukolov Chromium Russian proxy test",
                "port": PORT,
                "proxy_variables_present": {
                    key: bool(os.getenv(key))
                    for key in (
                        "PROXY_HOST",
                        "PROXY_PORT",
                        "PROXY_USER",
                        "PROXY_PASS"
                    )
                },
                "proxy_configured": bool(get_proxy())
            })
            return

        if self.path.startswith("/test"):
            with status_lock:
                already_running = status["running"]

            if not already_running:
                threading.Thread(target=worker, daemon=True).start()

            self.respond_json({
                "started": not already_running,
                "already_running": already_running
            })
            return

        if self.path.startswith("/status"):
            with status_lock:
                snapshot = dict(status)

            self.respond_json(snapshot)
            return

        self.respond_json({
            "project": "Glukolov Chromium Russian proxy test",
            "endpoints": ["/health", "/test", "/status"]
        })

    def log_message(self, fmt, *args):
        mark("HTTP " + (fmt % args))


mark(f"Glukolov Chromium Russian proxy test listening on port {PORT}")

ThreadingHTTPServer(
    ("0.0.0.0", PORT),
    Handler
).serve_forever()
