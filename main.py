import json
import os
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread


def check_url(url):
    result = {"url": url}

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/130.0.0.0 "
                    "Safari/537.36"
                ),
                "Accept-Language": "ru-RU,ru;q=0.9"
            }
        )

        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read(200000).decode(
                "utf-8", errors="replace"
            )

            result["http"] = response.status
            result["bytes"] = len(body)
            result["title"] = ""

            import re
            match = re.search(
                r"<title[^>]*>(.*?)</title>",
                body,
                re.I | re.S
            )
            if match:
                result["title"] = match.group(1).strip()[:200]

            result["captcha"] = (
                "captcha" in body.lower()
                or "robot-check" in body.lower()
            )

    except urllib.error.HTTPError as e:
        result["http"] = e.code
        result["error"] = str(e)

    except Exception as e:
        result["error"] = repr(e)

    return result


def run_test():
    report = {
        "project": "Glukolov",
        "ip": check_url("https://api.ipify.org?format=json"),
        "ozon": check_url("https://www.ozon.ru/")
    }
    return report


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            data = {"status": "ok"}
        else:
            data = run_test()

        body = json.dumps(
            data, ensure_ascii=False, indent=2
        ).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


port = int(os.environ.get("PORT", "8080"))
server = HTTPServer(("0.0.0.0", port), Handler)

print(f"Glukolov test started on port {port}", flush=True)
server.serve_forever()
