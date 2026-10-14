import os
import json
import time
import asyncio
import threading
import traceback
import glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PORT = int(os.environ.get("PORT", "8080"))

STATE = {
    "running": False,
    "result": None,
    "last_error": None,
}

LOCK = threading.Lock()


def browser_diagnostics():
    home = os.path.expanduser("~")
    cache = os.path.join(home, ".cache", "ms-playwright")

    return {
        "home": home,
        "browser_cache": cache,
        "browser_cache_exists": os.path.exists(cache),
        "browser_cache_contents": (
            os.listdir(cache)
            if os.path.exists(cache)
            else []
        ),
        "firefox_candidates": glob.glob(
            os.path.join(cache, "firefox-*", "**", "firefox"),
            recursive=True
        ),
    }


async def run_test():

    from playwright.async_api import async_playwright

    result = {
        "project": "Glukolov Firefox Ozon commit test",
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "diagnostics": browser_diagnostics(),
        "steps": []
    }

    def step(text):
        print(text, flush=True)
        result["steps"].append(text)

    async with async_playwright() as p:

        step("PLAYWRIGHT READY")

        proxy = None

        if os.environ.get("PROXY_HOST"):
            proxy = {
                "server": (
                    f"http://{os.environ['PROXY_HOST']}:"
                    f"{os.environ['PROXY_PORT']}"
                ),
                "username": os.environ.get("PROXY_USER", ""),
                "password": os.environ.get("PROXY_PASS", "")
            }

        step("FIREFOX START")

        browser = await p.firefox.launch(
            headless=True,
            proxy=proxy
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

        step("OPEN OZON")

        try:

            response = await page.goto(
                "https://www.ozon.ru/",
                wait_until="commit",
                timeout=60000
            )

            step("PAGE COMMITTED")

            await page.wait_for_timeout(15000)

            html = await page.content()

            title = await page.title()

            result["ozon"] = {
                "status": (
                    response.status
                    if response
                    else None
                ),
                "url": page.url,
                "title": title,
                "html_length": len(html),

                "markers": {
                    "challenge": (
                        "challenge"
                        in html.lower()
                        or "abt-challenge"
                        in html.lower()
                    ),

                    "connection_error": (
                        "нет соединения"
                        in html.lower()
                    ),

                    "antibot": (
                        "antibot"
                        in html.lower()
                    ),

                    "tilegrid": (
                        "tilegrid"
                        in html.lower()
                    ),

                    "pricev2": (
                        "pricev2"
                        in html.lower()
                    ),

                    "product": (
                        "product"
                        in html.lower()
                        or "товар"
                        in html.lower()
                    )
                },

                "html_start": html[:2000]
            }

            step("ANALYSIS COMPLETE")


        except Exception as e:

            result["navigation_error"] = {
                "type": type(e).__name__,
                "message": str(e),
            }

            try:
                html = await page.content()

                result["after_error"] = {
                    "url": page.url,
                    "title": await page.title(),
                    "html_length": len(html),
                    "html_start": html[:2000]
                }

            except Exception:
                pass


        await context.close()
        await browser.close()


    return result



def worker():

    try:

        result = asyncio.run(run_test())

        with LOCK:
            STATE["result"] = result
            STATE["last_error"] = None

        print("TEST FINISHED", flush=True)

    except Exception as e:

        err = {
            "type": type(e).__name__,
            "message": str(e),
            "traceback": traceback.format_exc()
        }

        with LOCK:
            STATE["last_error"] = err

        print("TEST ERROR", flush=True)
        print(err["traceback"], flush=True)

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

        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass


    def do_GET(self):

        path = urlparse(self.path).path


        if path == "/health":

            self.send_json({
                "status": "ok",
                "project": "Glukolov Firefox Ozon commit test"
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
                self.send_json(dict(STATE))


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
    f"Glukolov Firefox Ozon commit test listening {PORT}",
    flush=True
)


server = ThreadingHTTPServer(
    ("0.0.0.0", PORT),
    Handler
)

server.serve_forever()
