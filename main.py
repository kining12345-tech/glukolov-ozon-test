import asyncio
import json
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from playwright.async_api import async_playwright


PORT = 8080

status = {
    "running": False,
    "result": None,
    "last_error": None
}


def mark(text):
    print(text, flush=True)


async def chromium_test():

    result = {
        "project": "Glukolov Chromium Russian proxy test",
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "steps": []
    }

    proxy = None

    # если есть переменная окружения
    import os

    proxy_url = os.getenv("PROXY_URL")

    if proxy_url:
        proxy = {
            "server": proxy_url
        }

    result["proxy_configured"] = bool(proxy)

    async with async_playwright() as p:

        result["steps"].append("PLAYWRIGHT_READY")
        mark("PLAYWRIGHT READY")

        browser = await p.chromium.launch(
            headless=True,
            channel="chromium",
            proxy=proxy,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )

        result["steps"].append("CHROMIUM_STARTED")
        mark("CHROMIUM STARTED")


        context = await browser.new_context(
            user_agent=
            "Mozilla/5.0 (Android 13; Mobile) "
            "AppleWebKit/537.36 "
            "Chrome/141 Mobile Safari/537.36",
            locale="ru-RU",
            viewport={
                "width":390,
                "height":844
            }
        )

        page = await context.new_page()


        async def check(url):

            item = {
                "url": url
            }

            try:

                response = await page.goto(
                    url,
                    wait_until="commit",
                    timeout=15000
                )

                item["status"] = response.status if response else None
                item["final_url"] = page.url

                html = await page.content()

                item["html_length"] = len(html)

                item["markers"] = {
                    "challenge": "challenge" in html.lower(),
                    "antibot": "antibot" in html.lower(),
                    "tilegrid": "tilegrid" in html.lower(),
                    "pricev2": "priceV2" in html,
                    "product": "product" in html.lower()
                }

                item["title"] = await page.title()

            except Exception as e:

                item["error"] = str(e)
                item["current_url"] = page.url


            return item


        result["tests"] = {}

        result["tests"]["ipify"] = await check(
            "https://api.ipify.org/?format=json"
        )

        result["steps"].append("IP_DONE")


        result["tests"]["httpbin"] = await check(
            "https://httpbin.org/get"
        )

        result["steps"].append("HTTPBIN_DONE")


        result["tests"]["ozon"] = await check(
            "https://www.ozon.ru/"
        )

        result["steps"].append("OZON_DONE")


        await browser.close()

    result["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    return result



def worker():

    global status

    try:

        status["running"] = True
        status["last_error"] = None

        status["result"] = asyncio.run(
            chromium_test()
        )

    except Exception as e:

        status["last_error"] = {
            "type": type(e).__name__,
            "message": str(e),
            "traceback": traceback.format_exc()
        }

    finally:

        status["running"] = False



class Handler(BaseHTTPRequestHandler):

    def do_GET(self):

        global status

        if self.path.startswith("/health"):

            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b'{"status":"ok"}'
            )

            return


        if self.path.startswith("/test"):

            if not status["running"]:
                threading.Thread(
                    target=worker,
                    daemon=True
                ).start()

            self.send_response(200)
            self.end_headers()

            self.wfile.write(
                b'{"started":true}'
            )

            return


        if self.path.startswith("/status"):

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/json"
            )
            self.end_headers()

            self.wfile.write(
                json.dumps(
                    status,
                    ensure_ascii=False
                ).encode()
            )

            return


        self.send_response(200)
        self.end_headers()

        self.wfile.write(
            b"Glukolov Chromium test"
        )



print(
    "Glukolov Chromium test listening",
    PORT,
    flush=True
)


HTTPServer(
    ("0.0.0.0", PORT),
    Handler
).serve_forever()
