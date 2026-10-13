import os
import re
import json
import urllib.request
import urllib.error
import urllib.parse
import http.cookiejar
from html import unescape
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("PORT", "8080"))

PROXY_HOST = os.environ.get("PROXY_HOST", "")
PROXY_PORT = os.environ.get("PROXY_PORT", "")
PROXY_USER = os.environ.get("PROXY_USER", "")
PROXY_PASS = os.environ.get("PROXY_PASS", "")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def make_opener(cookies=None):
    if not all([PROXY_HOST, PROXY_PORT, PROXY_USER, PROXY_PASS]):
        raise RuntimeError("Proxy environment variables are missing")

    user = urllib.parse.quote(PROXY_USER, safe="")
    password = urllib.parse.quote(PROXY_PASS, safe="")
    proxy_url = f"http://{user}:{password}@{PROXY_HOST}:{PROXY_PORT}"

    handlers = [
        NoRedirect(),
        urllib.request.ProxyHandler({
            "http": proxy_url,
            "https": proxy_url,
        }),
    ]

    if cookies is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookies))

    return urllib.request.build_opener(*handlers)


def analyze_html(body):
    text = re.sub(r"(?is)<script\b.*?</script>", " ", body)
    text = re.sub(r"(?is)<style\b.*?</style>", " ", text)
    text = re.sub(r"(?is)<svg\b.*?</svg>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()

    title_match = re.search(
        r"(?is)<title[^>]*>(.*?)</title>",
        body
    )
    title = ""
    if title_match:
        title = re.sub(r"\s+", " ", unescape(title_match.group(1))).strip()

    markers = {
        "captcha": bool(re.search(r"captcha|капч|验证码", body, re.I)),
        "cloudflare": bool(re.search(
            r"cloudflare|cf-chl-|challenges.cloudflare.com",
            body,
            re.I
        )),
        "access_denied": bool(re.search(
            r"access denied|доступ ограничен|доступ запрещён|доступ запрещен",
            text,
            re.I
        )),
        "robot_check": bool(re.search(
            r"провер.*(робот|человек)|подтвердите, что вы человек|are you human|verify you are human",
            text,
            re.I
        )),
        "ozon_error": bool(re.search(
            r"что-то пошло не так|ошибка|попробуйте позже",
            text,
            re.I
        )),
        "product_signals": bool(re.search(
            r"добавить в корзину|купить|отзывы|рейтинг",
            text,
            re.I
        )),
    }

    return {
        "title": title[:300],
        "visible_text": text[:1800],
        "html_length": len(body),
        "markers": markers,
        "html_start": body[:500],
    }


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
        raw = response.read(50000)
        body = raw.decode("utf-8", "replace")

        return {
            "status": response.status,
            "url": response.geturl(),
            "location": response.headers.get("Location"),
            "content_type": response.headers.get("Content-Type"),
            "analysis": analyze_html(body),
        }

    except urllib.error.HTTPError as e:
        raw = e.read(50000)
        body = raw.decode("utf-8", "replace")

        return {
            "status": e.code,
            "location": e.headers.get("Location"),
            "content_type": e.headers.get("Content-Type"),
            "analysis": analyze_html(body),
        }

    except Exception as e:
        return {
            "error": type(e).__name__,
            "details": str(e)[:500],
        }


def run_test():
    results = {
        "project": "Glukolov proxy diagnostic v2",
        "proxy_endpoint": f"{PROXY_HOST}:{PROXY_PORT}",
    }

    try:
        opener = make_opener()
        results["external_ip"] = request_url(
            "https://api.ipify.org?format=json",
            opener
        )
    except Exception as e:
        results["external_ip"] = {
            "error": type(e).__name__,
            "details": str(e)[:500],
        }

    try:
        jar = http.cookiejar.CookieJar()
        opener = make_opener(jar)

        results["ozon_home_first"] = request_url(
            "https://www.ozon.ru/",
            opener
        )
        results["ozon_home_second"] = request_url(
            "https://www.ozon.ru/",
            opener
        )
        results["ozon_category"] = request_url(
            "https://www.ozon.ru/category/zhenskaya-odezhda-7501/",
            opener
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
            indent=2
        ).encode("utf-8")

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


print(f"Glukolov proxy diagnostic v2 listening on port {PORT}", flush=True)

HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
