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


async def run_test():

    from playwright.async_api import async_playwright

    result = {
        "project": "Glukolov Firefox hard timeout",
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "steps": []
    }

    def log(x):
        print(x, flush=True)
        result["steps"].append(x)

    async with async_playwright() as p:

        log("PLAYWRIGHT READY")

        browser = await p.firefox.launch(
            headless=True
        )

        log("FIREFOX STARTED")


        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Android 13; Mobile; rv:157.0) "
                "Gecko/157.0 Firefox/157.0"
            ),
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={
                "width": 412,
                "height": 915
            },
            has_touch=True
        )


        page = await context.new_page()

        log("PAGE CREATED")


        failed_requests = []

        page.on(
            "requestfailed",
            lambda req: failed_requests.append({
                "url": req.url,
                "error": str(req.failure)
            })
        )


        async def navigation():

            try:

                response = await page.goto(
                    "https://www.ozon.ru/",
                    wait_until="commit",
                    timeout=15000
                )

                return {
                    "status": (
                        response.status
                        if response
                        else None
                    ),
                    "result": "commit_ok"
                }

            except Exception as e:

                return {
                    "result": "error",
                    "error": str(e)
                }


        task = asyncio.create_task(
            navigation()
        )


        await asyncio.sleep(20)

        log("20 SECOND CHECK")


        try:
            html = await page.content()
        except Exception as e:
            html = "CONTENT ERROR: " + str(e)


        try:
            title = await page.title()
        except Exception as e:
            title = "TITLE ERROR: " + str(e)


        result["ozon"] = {

            "url": page.url,

            "title": title,

            "html_length": len(html),

            "markers": {

                "challenge":
                    "challenge" in html.lower()
                    or "abt-challenge" in html.lower(),

                "antibot":
                    "antibot" in html.lower(),

                "connection_error":
                    "нет соединения" in html.lower(),

                "tilegrid":
                    "tilegrid" in html.lower(),

                "pricev2":
                    "pricev2" in html.lower(),

                "product":
                    "product" in html.lower()
                    or "товар" in html.lower()
            },

            "html_start": html[:2000],

            "failed_requests": failed_requests[:20]
        }


        if task.done():

            result["navigation_task"] = (
                task.result()
            )

        else:

            result["navigation_task"] = (
                "still_running_after_20_seconds"
            )


        log("CLOSING BROWSER")


        try:
            await context.close()
        except:
            pass


        try:
            await browser.close()
        except:
            pass


        log("DONE")


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
                "status": "ok",
                "project": "Glukolov Firefox hard timeout"
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
                "status": "started"
            })


        elif path == "/status":

            with LOCK:

                self.send_json(
                    STATE
                )


        else:

            self.send_json({
                "routes": [
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
    f"Glukolov Firefox hard timeout listening {PORT}",
    flush=True
)


server = ThreadingHTTPServer(
    ("0.0.0.0", PORT),
    Handler
)

server.serve_forever()
