import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, quote

import requests

PORT = int(os.environ.get("PORT", "8000"))
HOST = "https://www.ozon.ru"

state_lock = threading.Lock()
state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "status": "IDLE",
    "results": [],
    "error": None,
}


def now():
    return datetime.now(timezone.utc).isoformat()


def proxy_url():
    direct = os.getenv("PROXY_URL")
    if direct:
        return direct

    host = os.getenv("PROXY_HOST")
    port = os.getenv("PROXY_PORT")

    if not host or not port:
        return None

    user = os.getenv("PROXY_USER")
    password = os.getenv("PROXY_PASS")

    if user:
        auth = quote(user, safe="")
        if password:
            auth += ":" + quote(password, safe="")
        return f"http://{auth}@{host}:{port}"

    return f"http://{host}:{port}"


def safe_path(value):
    if not value:
        return None
    try:
        parsed = urlparse(value)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        return path[:1500]
    except Exception:
        return None


def make_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/141.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/json,"
            "*/*;q=0.8"
        ),
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
    })

    proxy = proxy_url()
    if proxy:
        session.proxies.update({
            "http": proxy,
            "https": proxy,
        })

    return session


def cookie_summary(session):
    # Имена cookie можно показать; значения и токены не выводим.
    return [
        {
            "name": cookie.name,
            "domain": cookie.domain,
            "path": cookie.path,
            "secure": cookie.secure,
            "expires": cookie.expires,
        }
        for cookie in session.cookies
    ]


def response_info(response):
    body = response.text[:30000]
    lower = body.lower()

    headers = {}
    for key in (
        "Server",
        "Date",
        "Content-Type",
        "Content-Length",
        "Location",
        "Via",
        "X-Cache",
        "Retry-After",
    ):
        value = response.headers.get(key)
        if value is not None:
            headers[key] = value[:1000]

    return {
        "status": response.status_code,
        "url_path": safe_path(response.url),
        "location": safe_path(response.headers.get("Location")),
        "headers": headers,
        "body_length": len(response.content),
        "body_preview": body[:500],
        "markers": {
            "rr": "__rr=" in (
                response.headers.get("Location", "") + body
            ),
            "widget_states": "widgetStates" in body,
            "tile_grid": "tileGrid2" in body,
            "price_v2": "priceV2" in body,
            "captcha": "captcha" in lower,
            "challenge": "challenge" in lower,
        },
    }


def request_no_cookie(url):
    session = make_session()
    session.cookies.clear()

    response = session.get(
        url,
        timeout=15,
        allow_redirects=False,
    )

    return {
        "scenario": "no_cookie",
        "response": response_info(response),
        "cookie_count_after": len(session.cookies),
        "cookies_after": cookie_summary(session),
    }


def request_session_once(url):
    session = make_session()

    first = session.get(
        url,
        timeout=15,
        allow_redirects=False,
    )

    first_info = response_info(first)
    cookies_after_first = cookie_summary(session)

    second = session.get(
        url,
        timeout=15,
        allow_redirects=False,
    )

    return {
        "scenario": "session_two_requests",
        "first_response": first_info,
        "cookies_after_first": cookies_after_first,
        "second_response": response_info(second),
        "cookies_after_second": cookie_summary(session),
    }


def request_follow_redirects(url):
    session = make_session()

    response = session.get(
        url,
        timeout=20,
        allow_redirects=True,
        max_redirects=5,
    )

    return {
        "scenario": "follow_redirects",
        "final_response": response_info(response),
        "redirect_history": [
            response_info(item)
            for item in response.history
        ],
        "cookies": cookie_summary(session),
    }


def run_tests():
    targets = {
        "home": HOST + "/",
        "catalog_api": (
            HOST
            + "/api/entrypoint-api.bx/page/json/v2"
            + "?url=%2Fcategory%2Fbluzy-i-rubashki-zhenskie-7511%2F"
        ),
    }

    results = []

    try:
        for name, url in targets.items():
            for test_func in (
                request_no_cookie,
                request_session_once,
                request_follow_redirects,
            ):
                item = {
                    "target": name,
                    "test": test_func.__name__,
                    "started_at": now(),
                }

                try:
                    item["result"] = test_func(url)
                except Exception as exc:
                    item["error_type"] = type(exc).__name__
                    item["error"] = str(exc)[:400]

                item["finished_at"] = now()
                results.append(item)

                with state_lock:
                    state["results"] = list(results)

                time.sleep(0.5)

        with state_lock:
            state["status"] = "COMPLETED"
            state["finished_at"] = now()

    except Exception as exc:
        with state_lock:
            state["status"] = "FAILED"
            state["error"] = (
                f"{type(exc).__name__}: {str(exc)[:400]}"
            )
            state["finished_at"] = now()

    finally:
        with state_lock:
            state["running"] = False


class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, code=200):
        body = json.dumps(
            data,
            ensure_ascii=False,
        ).encode("utf-8")

        self.send_response(code)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            self.send_json({
                "ok": True,
                "proxy_configured": bool(proxy_url()),
            })
            return

        if path == "/test-http":
            with state_lock:
                if state["running"]:
                    self.send_json({
                        "started": False,
                        "message": "Tests already running",
                    })
                    return

                state.update({
                    "running": True,
                    "started_at": now(),
                    "finished_at": None,
                    "status": "RUNNING",
                    "results": [],
                    "error": None,
                })

            threading.Thread(
                target=run_tests,
                daemon=True,
            ).start()

            self.send_json({
                "started": True,
                "status_url": "/status",
            })
            return

        if path == "/status":
            with state_lock:
                snapshot = json.loads(
                    json.dumps(state, ensure_ascii=False)
                )
            self.send_json(snapshot)
            return

        self.send_json({
            "endpoints": [
                "/health",
                "/test-http",
                "/status",
            ]
        })


print(
    f"Glukolov cookie diagnostics started on port {PORT}",
    flush=True,
)

ThreadingHTTPServer(
    ("0.0.0.0", PORT),
    Handler,
).serve_forever()
