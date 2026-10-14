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
PROXY_PASS = os.environ.get("PROXY_PASS", "")

PROFILE_DIR = "/tmp/glukolov-chrome-profile"
RESULT_FILE = "/tmp/result.json"


def now():
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime()
    )


def save(data):
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


def load():
    if not os.path.exists(RESULT_FILE):
        return {
            "status": "idle"
        }

    with open(
        RESULT_FILE,
        encoding="utf-8"
    ) as f:
        return json.load(f)



async def inspect(page):

    print(
        "OPEN OZON",
        flush=True
    )

    result = {}

    try:

        response = await page.goto(
            "https://www.ozon.ru/",
            wait_until="domcontentloaded",
            timeout=30000
        )


        print(
            "PAGE LOADED",
            flush=True
        )


        result["status"] = (
            response.status
            if response
            else None
        )

        result["url"] = page.url


        await page.wait_for_timeout(8000)


        html = await page.content()


        result["html_length"] = len(html)


        result["cookies"] = await page.context.cookies()


        low = html.lower()


        result["markers"] = {

            "antibot":
                "antibot challenge" in low,

            "robot":
                "не робот" in low
                or "robot" in low,

            "tilegrid":
                "tilegrid2" in low,

            "pricev2":
                "pricev2" in low,

            "product":
                "/product/" in low,

            "ozon_main":
                "ozon" in low

        }


        result["html_start"] = html[:1500]


        print(
            "HTML:",
            len(html),
            flush=True
        )


    except Exception as e:

        result["error"] = str(e)


    return result



async def run_browser():

    print(
        "CHROMIUM START",
        flush=True
    )

    output = {
        "project":
            "Glukolov persistent Chromium test"
    }


    async with async_playwright() as p:


        browser = None


        try:

            browser = await p.chromium.launch_persistent_context(

                user_data_dir=PROFILE_DIR,

                executable_path="/usr/bin/chromium",

                headless=True,

                proxy={

                    "server":
                        f"http://{PROXY_HOST}:{PROXY_PORT}",

                    "username":
                        PROXY_USER,

                    "password":
                        PROXY_PASS

                },

                viewport={
                    "width":1365,
                    "height":900
                },

                locale="ru-RU",

                timezone_id="Europe/Moscow",

                user_agent=(

                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"

                ),

                args=[

                    "--no-sandbox",

                    "--disable-blink-features=AutomationControlled"

                ]

            )


            print(
                "PROFILE READY",
                flush=True
            )


            await browser.add_init_script(

                """

                Object.defineProperty(
                    navigator,
                    'webdriver',
                    {
                        get: () => undefined
                    }
                );


                Object.defineProperty(
                    navigator,
                    'languages',
                    {
                        get: () => ['ru-RU','ru']
                    }
                );


                Object.defineProperty(
                    navigator,
                    'platform',
                    {
                        get: () => 'Win32'
                    }
                );

                """

            )


            page = (
                browser.pages[0]
                if browser.pages
                else await browser.new_page()
            )


            output["ozon"] = await inspect(page)



        except Exception as e:

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

    save({

        "status":"running",

        "started_at":now(),

        "result":None

    })


    result = asyncio.run(
        run_browser()
    )


    save({

        "status":"completed",

        "finished_at":now(),

        "result":result

    })




class Handler(BaseHTTPRequestHandler):


    def send(self,data):

        raw=json.dumps(
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

        path=urlparse(
            self.path
        ).path


        if path=="/health":

            return self.send({
                "status":"ok"
            })


        if path=="/test":

            threading.Thread(
                target=worker,
                daemon=True
            ).start()


            return self.send({
                "status":"started"
            })


        if path=="/status":

            return self.send(
                load()
            )


        return self.send({
            "error":"404"
        })


    def log_message(self,*args):
        pass



print(
    f"Glukolov persistent Chromium test listening on {PORT}",
    flush=True
)


HTTPServer(
    ("0.0.0.0",PORT),
    Handler
).serve_forever()
