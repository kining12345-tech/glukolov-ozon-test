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
        "project": "Glukolov Firefox Ozon timeout debug",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "steps": []
    }

    def step(x):
        print(x, flush=True)
        result["steps"].append(x)

    async with async_playwright() as p:

        step("PLAYWRIGHT READY")

        browser = await p.firefox.launch(
            headless=True
        )

        step("FIREFOX READY")

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

        failed = []

        page.on(
            "requestfailed",
            lambda req: failed.append({
                "url": req.url,
                "error": str(req.failure)
            })
        )

        step("OPEN OZON")

        try:
            await page.goto(
                "https://www.ozon.ru/",
                wait_until="commit",
                timeout=25000
            )

            navigation = "commit_ok"

        except Exception as e:
            navigation = str(e)

        step("NAVIGATION FINISHED")

        await page.wait_for_timeout(3000)

        try:
            html = await page.content()
        except Exception:
            html = ""

        try:
            title = await page.title()
        except Exception:
            title = ""

        result["ozon"] = {
            "navigation": navigation,
            "url": page.url,
            "title": title,
            "html_length": len(html),

            "markers": {
                "challenge":
                    "challenge" in html.lower()
                    or "abt-challenge" in html.lower(),

                "connection_error":
                    "нет соединения" in html.lower(),

                "antibot":
                    "antibot" in html.lower(),

                "tilegrid":
                    "tilegrid" in html.lower(),

                "pricev2":
                    "pricev2" in html.lower(),

                "product":
                    "product" in html.lower()
                    or "товар" in html.lower()
            },

            "failed_requests": failed[:20],
            "html_start": html[:2000]
        }

        await context.close()
        await browser.close()

        step("DONE")

    return result


def worker():

    try:
        result = asyncio.run(run_test())

        with LOCK:
            STATE["result"] = result
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

        path = urlparse(self.path).path

        if path == "/health":

            self.send_json({
                "status": "ok",
                "project": "Glukolov Firefox test"
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
                self.send_json(STATE)

        else:

            self.send_json({
                "routes": [
                    "/health",
                    "/test",
                    "/status"
                ]
            })


    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


print(
    f"Glukolov Firefox test listening {PORT}",
    flush=True
)

server = ThreadingHTTPServer(
    ("0.0.0.0", PORT),
    Handler
)

server.serve_forever()
