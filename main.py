import os
import json
import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from playwright.async_api import async_playwright


PORT = int(os.environ.get("PORT", "8080"))

PROXY_HOST = os.environ.get("PROXY_HOST", "")
PROXY_PORT = os.environ.get("PROXY_PORT", "")
PROXY_USER = os.environ.get("PROXY_USER", "")
PROXY_PASS = os.environ.get("PROXY_PASS")

RESULT_FILE = "/tmp/glukolov_result.json"


def now():
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime()
    )


def save_result(data):
    with open(
        RESULT_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


def load_result():
    if not os.path.exists(RESULT_FILE):
        return {
            "status": "idle",
            "started_at": None,
            "finished_at": None,
            "result": None,
        }

    with open(
        RESULT_FILE,
        "r",
        encoding="utf-8"
    ) as f:
        return json.load(f)


async def check_page(page, url):

    print("OPEN:", url, flush=True)

    result = {
        "url": url
    }

    try:
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        result["status"] = (
            response.status
            if response
            else None
        )

        result["final_url"] = page.url

        await page.wait_for_timeout(5000)

        result["title"] = await page.title()

        text = await page.locator(
            "body"
        ).inner_text(
            timeout=15000
        )

        result["text"] = (
            " ".join(text.split())
        )[:3000]

        low = text.lower()

        result["markers"] = {
            "connection_error":
                "нет соединения" in low,

            "captcha":
                "captcha" in low
                or "капч" in low,

            "products":
                any(
                    x in low
                    for x in [
                        "купить",
                        "корзина",
                        "отзывы",
                        "рейтинг"
                    ]
                )
        }

    except Exception as e:
        result["error"] = type(e).__name__
        result["details"] = str(e)

    return result


async def browser_test():

    print("CHROMIUM START", flush=True)

    output = {
        "project":
            "Glukolov Chromium proxy test",
        "proxy":
            f"{PROXY_HOST}:{PROXY_PORT}"
    }

    async with async_playwright() as p:

        browser = None

        try:

            browser = await p.chromium.launch(
                executable_path="/usr/bin/chromium",
                headless=True,
                proxy={
                    "server":
                        f"http://{PROXY_HOST}:{PROXY_PORT}",
                    "username":
                        PROXY_USER,
                    "password":
                        PROXY_PASS,
                },
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )

            print(
                "CHROMIUM READY",
                flush=True
            )

            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 "
                    "(Linux; Android 13) "
                    "Chrome/130 Mobile"
                ),
                viewport={
                    "width":412,
                    "height":915
                },
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                is_mobile=True,
            )

            page = await context.new_page()


            print(
                "OZON TEST",
                flush=True
            )


            output["ozon"] = await check_page(
                page,
                "https://www.ozon.ru/"
            )


            if output["ozon"].get("status") == 200:

                output["category"] = await check_page(
                    page,
                    "https://www.ozon.ru/category/zhenskaya-odezhda-7501/"
                )


            await context.close()


        except Exception as e:

            print(
                "ERROR:",
                e,
                flush=True
            )

            output["error"] = type(e).__name__
            output["details"] = str(e)


        finally:

            if browser:
                await browser.close()


    print(
        "TEST FINISH",
        flush=True
    )

    return output



def worker():

    try:

        result = asyncio.run(
            browser_test()
        )

        save_result({
            "status": "completed",
            "finished_at": now(),
            "result": result
        })


    except Exception as e:

        save_result({
            "status": "failed",
            "finished_at": now(),
            "result": {
                "error": str(e)
            }
        })



class Handler(BaseHTTPRequestHandler):

    def json(self, data):

        raw = json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ).encode()

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "application/json"
        )

        self.send_header(
            "Content-Length",
            str(len(raw))
        )

        self.end_headers()

        try:
            self.wfile.write(raw)
        except:
            pass


    def do_GET(self):

        path = urlparse(
            self.path
        ).path


        if path == "/health":

            return self.json({
                "status":"ok"
            })


        if path == "/":

            return self.json({
                "project":
                    "Glukolov Chromium proxy test",
                "routes":[
                    "/test",
                    "/status"
                ]
            })


        if path == "/test":

            save_result({
                "status":"running",
                "started_at":now(),
                "finished_at":None,
                "result":None
            })


            threading.Thread(
                target=worker,
                daemon=True
            ).start()


            return self.json({
                "status":"started"
            })


        if path == "/status":

            return self.json(
                load_result()
            )


        return self.json({
            "error":"404"
        })


    def log_message(
        self,
        fmt,
        *args
    ):
        print(
            fmt % args,
            flush=True
        )



print(
    "Glukolov Chromium proxy test listening on",
    PORT,
    flush=True
)


HTTPServer(
    ("0.0.0.0",PORT),
    Handler
).serve_forever()
