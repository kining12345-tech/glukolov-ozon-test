import os
import json
import time
import asyncio
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PORT = int(os.environ.get("PORT", "8080"))

STATE = {
    "running": False,
    "result": None,
    "last_error": None
}

LOCK = threading.Lock()


async def fetch_page(page, url, timeout=20000):

    result = {
        "url": url
    }

    try:

        response = await page.goto(
            url,
            wait_until="commit",
            timeout=timeout
        )

        await page.wait_for_timeout(2000)

        try:
            html = await page.content()
        except:
            html = ""

        result.update({
            "status": (
                response.status
                if response
                else None
            ),
            "final_url": page.url,
            "title": await page.title(),
            "html_length": len(html),
            "html_start": html[:500]
        })


    except Exception as e:

        result["error"] = str(e)

        try:
            result["current_url"] = page.url
        except:
            pass


    return result



async def run_test():

    from playwright.async_api import async_playwright


    result = {
        "project": "Glukolov Firefox network comparison",
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }


    async with async_playwright() as p:


        browser = await p.firefox.launch(
            headless=True
        )


        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Android 13; Mobile; rv:157.0) "
                "Gecko/157.0 Firefox/157.0"
            ),
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={
                "width":412,
                "height":915
            },
            has_touch=True
        )


        page = await context.new_page()


        result["tests"] = {}


        print("TEST IPIFY", flush=True)

        result["tests"]["ipify"] = await fetch_page(
            page,
            "https://api.ipify.org/?format=json"
        )


        print("TEST HTTPBIN", flush=True)

        result["tests"]["httpbin"] = await fetch_page(
            page,
            "https://httpbin.org/get"
        )


        print("TEST OZON", flush=True)

        result["tests"]["ozon"] = await fetch_page(
            page,
            "https://www.ozon.ru/",
            timeout=30000
        )


        await context.close()
        await browser.close()


    return result



def worker():

    try:

        res = asyncio.run(
            run_test()
        )

        with LOCK:

            STATE["result"] = res
            STATE["last_error"] = None


    except Exception as e:

        with LOCK:

            STATE["last_error"] = {
                "type": type(e).__name__,
                "message": str(e),
                "traceback": traceback.format_exc()
            }


    finally:

        with LOCK:
            STATE["running"] = False



class Handler(BaseHTTPRequestHandler):


    def send_json(self, data):

        body = json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ).encode()


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

        self.wfile.write(body)



    def do_GET(self):

        path = urlparse(
            self.path
        ).path


        if path == "/health":

            self.send_json({
                "status":"ok",
                "project":"Glukolov Firefox network comparison"
            })


        elif path == "/test":

            with LOCK:

                if not STATE["running"]:

                    STATE["running"] = True
                    STATE["result"] = None
                    STATE["last_error"] = None


                    threading.Thread(
                        target=worker,
                        daemon=True
                    ).start()


            self.send_json({
                "status":"started"
            })


        elif path == "/status":

            with LOCK:
                self.send_json(
                    STATE
                )


        else:

            self.send_json({
                "routes":[
                    "/health",
                    "/test",
                    "/status"
                ]
            })



    def log_message(self, fmt, *args):
        print(
            fmt % args,
            flush=True
        )



print(
    f"Glukolov Firefox network comparison listening {PORT}",
    flush=True
)


server = ThreadingHTTPServer(
    ("0.0.0.0", PORT),
    Handler
)

server.serve_forever()
