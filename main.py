import os
import json
import urllib.request
import urllib.error
import urllib.parse
import http.cookiejar
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("PORT", "8080"))

PROXY_HOST = os.environ.get("PROXY_HOST", "")
PROXY_PORT = os.environ.get("PROXY_PORT", "")
PROXY_USER = os.environ.get("PROXY_USER", "")
PROXY_PASS = os.environ.get("PROXY_PASS", "")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def make_opener(use_proxy=True, cookies=None):
    handlers = [NoRedirect()]

    if use_proxy:
        if not all([PROXY_HOST, PROXY_PORT, PROXY_USER, PROXY_PASS]):
            raise RuntimeError("Proxy environment variables are missing")

        credentials = urllib.parse.quote(PROXY_USER, safe="")
        password = urllib.parse.quote(PROXY_PASS, safe="")

        proxy_url = (
            f"http://{credentials}:{password}"
            f"@{PROXY_HOST}:{PROXY_PORT}"
        )

        handlers.append(
            urllib.request.ProxyHandler({
                "http": proxy_url,
                "https": proxy_url,
            })
        )

    if cookies is not None:
        handlers.append(
            urllib.request.HTTPCookieProcessor(cookies)
        )

    return urllib.request.build_opener(*handlers)


def request_url(url, opener):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
        },
    )

    try:
        response = opener.open(req, timeout=30)
        body = response.read(12000).decode("utf-8", "replace")

        return {
            "status": response.status,
            "final_url": response.geturl(),
            "location": response.headers.get("Location"),
            "content_type": response.headers.get("Content-Type"),
            "body_bytes": len(body),
            "body_sample": body[:2500],
        }

    except urllib.error.HTTPError as e:
        body = e.read(12000).decode("utf-8", "replace")

        return {
            "status": e.code,
            "location": e.headers.get("Location"),
            "content_type": e.headers.get("Content-Type"),
            "set_cookie": e.headers.get_all("Set-Cookie"),
            "body_bytes": len(body),
            "body_sample": body[:2500],
        }

    except Exception as e:
        return {
            "error": type(e).__name__,
            "details": str(e)[:500],
        }


def run_test():
    results = {
        "project": "Glukolov proxy test",
        "proxy_endpoint": f"{PROXY_HOST}:{PROXY_PORT}",
    }

    # 1. Проверяем внешний IP через прокси
    try:
        opener = make_opener(use_proxy=True)
        results["external_ip"] = request_url(
            "https://api.ipify.org?format=json",
            opener,
        )
    except Exception as e:
        results["external_ip"] = {
            "error": type(e).__name__,
            "details": str(e)[:500],
        }

    # 2. Проверяем страну выхода, если сервис доступен
    try:
        opener = make_opener(use_proxy=True)
        results["ip_country"] = request_url(
            "https://ipapi.co/json/",
            opener,
        )
    except Exception as e:
        results["ip_country"] = {
            "error": type(e).__name__,
            "details": str(e)[:500],
        }

    # 3. Проверяем Ozon с сохранением cookies
    try:
        jar = http.cookiejar.CookieJar()
        opener = make_opener(use_proxy=True, cookies=jar)

        results["ozon_home_first"] = request_url(
            "https://www.ozon.ru/",
            opener,
        )

        results["ozon_home_second"] = request_url(
            "https://www.ozon.ru/",
            opener,
        )

        results["ozon_category"] = request_url(
            "https://www.ozon.ru/category/zhenskaya-odezhda-7501/",
            opener,
        )

    except Exception as e:
        results["ozon_error"] = {
            "error": type(e).__name__,
            "details": str(e)[:500],
        }

    return results


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            payload = {"status": "ok"}
        else:
            payload = run_test()

        data = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


print(f"Glukolov proxy test listening on port {PORT}", flush=True)

server = HTTPServer(("0.0.0.0", PORT), Handler)
server.serve_forever()
