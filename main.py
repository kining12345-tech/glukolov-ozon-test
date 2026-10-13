import json
import os
import threading
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from http.cookiejar import CookieJar

PORT = int(os.environ.get("PORT", "8080"))

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def check_url(url, cookie_jar=None):
    handlers = [NoRedirect()]
    if cookie_jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookie_jar))

    opener = urllib.request.build_opener(*handlers)
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
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
        },
    )

    try:
        response = opener.open(req, timeout=20)
        body = response.read(2000).decode("utf-8", "replace")
        return {
            "status": response.status,
            "url": response.geturl(),
            "location": response.headers.get("Location"),
            "content_type": response.headers.get("Content-Type"),
            "body_bytes_read": len(body),
            "title": body[:1000],
        }
    except urllib.error.HTTPError as e:
        body = e.read(2000).decode("utf-8", "replace")
        return {
            "status": e.code,
            "url": url,
            "location": e.headers.get("Location"),
            "set_cookie": e.headers.get_all("Set-Cookie"),
            "content_type": e.headers.get("Content-Type"),
            "body": body[:1000],
        }
    except Exception as e:
        return {"error": repr(e), "url": url}

def run_checks():
    results = {}

    # Проверяем главную без перехода по редиректу
    results["home_first_response"] = check_url("https://www.ozon.ru/")

    # Проверяем главную с сохранением cookie между запросами
    jar = CookieJar()
    results["home_with_cookies"] = check_url(
        "https://www.ozon.ru/", jar
    )
    results["home_second_with_cookies"] = check_url(
        "https://www.ozon.ru/", jar
    )

    # Отдельная проверка страницы категории
    results["category"] = check_url(
        "https://www.ozon.ru/category/zhenskaya-odezhda-7501/",
        jar
    )

    # Проверяем, удалось ли получить настоящую страницу каталога
    for name, result in results.items():
        body = result.get("body", "") + result.get("title", "")
        result["product_page_markers"] = any(
            marker in body.lower()
            for marker in (
                "tilegrid2",
                "product-card",
                "ozon.ru/product/",
                "pricev2",
            )
        )

    return results

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            payload = {"status": "ok"}
        else:
            payload = run_checks()

        data = json.dumps(
            payload, ensure_ascii=False, indent=2
        ).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(fmt % args)

server = HTTPServer(("0.0.0.0", PORT), Handler)
print(f"Glukolov network test listening on port {PORT}", flush=True)
server.serve_forever()

# network-test-v2
