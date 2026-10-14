import os
import json
import asyncio
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from playwright.async_api import async_playwright


print("=== GLUKOLOV START ===", flush=True)


PORT = int(os.environ.get("PORT", "8000"))

PROXY_HOST = os.environ.get("PROXY_HOST", "")
PROXY_PORT = os.environ.get("PROXY_PORT", "")
PROXY_USER = os.environ.get("PROXY_USER", "")
PROXY_PASS = os.environ.get("PROXY_PASS", "")


TEST_STATE = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "result": None,
}


def now():
    return datetime.now(
        timezone.utc
    ).isoformat()


async def ozon_test():

    print("CHROMIUM START", flush=True)

    result = {
        "project": "Glukolov Chromium proxy test"
    }


    async with async_playwright() as p:

        print("PLAYWRIGHT READY", flush=True)


        browser = await p.chromium.launch(
            executable_path="/usr/bin/chromium",
            headless=True,
            proxy={
                "server":
                    f"http://{PROXY_HOST}:{PROXY_PORT}",
                "username": PROXY_USER,
                "password": PROXY_PASS,
            },
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )


        print("BROWSER READY", flush=True)


        context = await browser.new_context(
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={
                "width": 1280,
                "height": 900,
            },
        )


        page = await context.new_page()


        print(
            "OPEN OZON",
            flush=True
        )


        ozon = {}


        try:

            response = await page.goto(
                "https://www.ozon.ru/",
                wait_until="commit",
                timeout=60000,
            )


            print(
                "PAGE COMMITTED",
                flush=True
            )


            ozon["http_status"] = (
                response.status
                if response
                else None
            )


            await page.wait_for_timeout(10000)


            print(
                "READ PAGE",
                flush=True
            )


            ozon["url"] = page.url

            ozon["title"] = (
                await page.title()
            )


            html = await page.content()


            ozon["html_length"] = len(html)

            low = html.lower()


            ozon["markers"] = {

                "antibot":
                    "antibot" in low,

                "challenge":
                    "challenge" in low,

                "captcha":
                    "captcha" in low,

                "tilegrid":
                    "tilegrid2" in low,

                "pricev2":
                    "pricev2" in low,

                "product":
                    "/product/" in low,
            }


            ozon["html_start"] = html[:1500]


        except Exception as e:

            print(
                "OZON ERROR:",
                repr(e),
                flush=True
            )

            ozon["error"] = repr(e)


        result["ozon"] = ozon


        await context.close()

        await browser.close()


    return result



def background_test():

    async def runner():

        try:

            TEST_STATE["status"] = "running"
            TEST_STATE["started_at"] = now()
            TEST_STATE["finished_at"] = None
            TEST_STATE["result"] = None


            data = await ozon_test()


            TEST_STATE["result"] = data
            TEST_STATE["status"] = "completed"


        except Exception as e:

            TEST_STATE["status"] = "error"

            TEST_STATE["result"] = {
                "error":
                    type(e).__name__,
                "details":
                    str(e)
            }


        finally:

            TEST_STATE["finished_at"] = now()


    asyncio.run(runner())



class Handler(BaseHTTPRequestHandler):


    def do_GET(self):

        if self.path.startswith("/health"):

            data = {
                "status": "ok",
                "project":
                    "Glukolov Chromium proxy test",
            }


        elif self.path.startswith("/test"):

            if TEST_STATE["status"] != "running":

                threading.Thread(
                    target=background_test,
                    daemon=True,
                ).start()


            data = {
                "status": "started",
                "check":
                    "/status"
            }


        elif self.path.startswith("/status"):

            data = TEST_STATE


        else:

            data = {
                "project":
                    "Glukolov Chromium proxy test",
                "routes": {
                    "health":
                        "/health",
                    "start":
                        "/test",
                    "status":
                        "/status",
                }
            }


        body = json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")


        self.send_response(200)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )

        self.send_header(
            "Content-Length",
            str(len(body)),
        )

        self.end_headers()


        try:

            self.wfile.write(body)

        except BrokenPipeError:

            pass



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
    f"=== LISTEN {PORT} ===",
    flush=True
)


try:

    HTTPServer(
        (
            "0.0.0.0",
            PORT,
        ),
        Handler,
    ).serve_forever()


except Exception as e:

    print(
        "SERVER ERROR:",
        repr(e),
        flush=True
    )

    raise
