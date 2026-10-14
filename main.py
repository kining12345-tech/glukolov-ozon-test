import os
import json
import time
import threading
import traceback
import requests

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


PORT = int(os.environ.get("PORT", "8080"))

STATE = {
    "running": False,
    "result": None,
    "last_error": None
}

LOCK = threading.Lock()


HEADERS = {
    "User-Agent":
        "Mozilla/5.0 (Android 13; Mobile; rv:157.0) "
        "Gecko/157.0 Firefox/157.0",

    "Accept":
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8",

    "Accept-Language":
        "ru-RU"
}



def check_url(url, timeout=20):

    result = {
        "url": url
    }

    try:

        r = requests.get(
            url,
            headers=HEADERS,
            timeout=timeout,
            allow_redirects=True
        )


        text = r.text[:2000]


        result.update({

            "status":
                r.status_code,

            "final_url":
                r.url,

            "content_type":
                r.headers.get(
                    "content-type"
                ),

            "length":
                len(r.text),


            "markers": {

                "challenge":
                    "challenge"
                    in r.text.lower(),

                "antibot":
                    "antibot"
                    in r.text.lower(),

                "connection_error":
                    "нет соединения"
                    in r.text.lower(),

                "product":
                    "product"
                    in r.text.lower(),

                "tilegrid":
                    "tilegrid"
                    in r.text.lower()
            },


            "body_start":
                text

        })


    except Exception as e:

        result["error"] = str(e)


    return result



def run_test():

    result = {

        "project":
            "Glukolov Python network test",

        "started_at":
            time.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "tests": {}

    }


    result["tests"]["ipify"] = check_url(
        "https://api.ipify.org/?format=json"
    )


    result["tests"]["httpbin"] = check_url(
        "https://httpbin.org/get"
    )


    result["tests"]["ozon"] = check_url(
        "https://www.ozon.ru/"
    )


    return result



def worker():

    try:

        res = run_test()

        with LOCK:

            STATE["result"] = res
            STATE["last_error"] = None


    except Exception as e:

        with LOCK:

            STATE["last_error"] = {

                "type":
                    type(e).__name__,

                "message":
                    str(e),

                "traceback":
                    traceback.format_exc()
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

                "status":
                    "ok",

                "project":
                    "Glukolov Python network test"

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
                "status":
                    "started"
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
    f"Glukolov Python network test listening {PORT}",
    flush=True
)


server = ThreadingHTTPServer(
    ("0.0.0.0", PORT),
    Handler
)

server.serve_forever()
