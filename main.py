import os
import json
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer

from playwright.async_api import async_playwright

PORT = int(os.environ.get("PORT", "8080"))

PROXY_HOST = os.environ.get("PROXY_HOST", "")
PROXY_PORT = os.environ.get("PROXY_PORT", "")
PROXY_USER = os.environ.get("PROXY_USER", "")
PROXY_PASS = os.environ.get("PROXY_PASS", "")


async def run_test():

    result = {
        "project": "Glukolov Playwright Firefox test"
    }

    async with async_playwright() as p:

        browser = None

        try:
            print("FIREFOX START", flush=True)

            browser = await p.firefox.launch(
                headless=True,
                proxy={
                    "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
                    "username": PROXY_USER,
                    "password": PROXY_PASS,
                },
            )

            print("FIREFOX READY", flush=True)

            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Android 13; Mobile; rv:157.0) "
                    "Gecko/157.0 Firefox/157.0"
                ),
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                viewport={
                    "width": 412,
                    "height": 915,
                },
                is_mobile=True,
                has_touch=True,
            )

            page = await context.new_page()

            print("OPEN OZON", flush=True)

            response = await page.goto(
                "https://www.ozon.ru/",
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await page.wait_for_timeout(5000)

            html = await page.content()

            title = await page.title()

            result["ozon"] = {
                "status": response.status if response else None,
                "url": page.url,
                "title": title,
                "html_length": len(html),
                "markers": {
                    "challenge": (
                        "Antibot Challenge" in html
                        or "abt-challenge" in html
                    ),
                    "tilegrid": (
                        "tileGrid2" in html
                    ),
                    "pricev2": (
                        "priceV2" in html
                    ),
                    "product": (
                        "Добавить в корзину" in html
                        or "Купить" in html
                    ),
                },
                "html_start": html[:1000],
            }

            await context.close()

        except Exception as e:

            result["error"] = type(e).__name__
            result["details"] = str(e)[:2000]

        finally:
            if browser:
                await browser.close()

    return result



STATE = {
    "result": None,
    "running": False
}


def start_async_test():

    async def worker():
        STATE["running"] = True
        STATE["result"] = await run_test()
        STATE["running"] = False

    asyncio.run(worker())



class Handler(BaseHTTPRequestHandler):

    def do_GET(self):

        if self.path.startswith("/health"):
            data = {
                "status": "ok",
                "project": "Glukolov Firefox test"
            }

        elif self.path.startswith("/test"):

            if not STATE["running"]:
                import threading
                threading.Thread(
                    target=start_async_test,
                    daemon=True
                ).start()

            data = {
                "status": "started"
            }

        elif self.path.startswith("/status"):

            data = {
                "running": STATE["running"],
                "result": STATE["result"]
            }

        else:

            data = {
                "routes": [
                    "/health",
                    "/test",
                    "/status"
                ]
            }


        body = json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ).encode("utf-8")


        self.send_response(200)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.end_headers()

        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass


    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)



print(
    f"Glukolov Firefox test listening {PORT}",
    flush=True
)


HTTPServer(
    ("0.0.0.0", PORT),
    Handler
).serve_forever()
