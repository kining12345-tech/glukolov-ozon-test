import os
import json
import time
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin, urlparse

import requests
from curl_cffi import requests as curl_requests


PORT = int(os.getenv("PORT", "8080"))
TIMEOUT = 20
MAX_REDIRECTS = 5

PROXY_HOST = os.getenv("PROXY_HOST", "")
PROXY_PORT = os.getenv("PROXY_PORT", "")
PROXY_USER = os.getenv("PROXY_USER", "")
PROXY_PASS = os.getenv("PROXY_PASS", "")
PROXY_URL = os.getenv("PROXY_URL", "")

if not PROXY_URL and PROXY_HOST and PROXY_PORT:
    if PROXY_USER and PROXY_PASS:
        PROXY_URL = (
            f"http://{PROXY_USER}:{PROXY_PASS}"
            f"@{PROXY_HOST}:{PROXY_PORT}"
        )
    else:
        PROXY_URL = f"http://{PROXY_HOST}:{PROXY_PORT}"


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/json,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

TARGETS = {
    "home": "https://www.ozon.ru/",
    "catalog_api": (
        "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2"
        "?url=%2Fcategory%2Fbluzy-i-rubashki-zhenskie-7511%2F"
    ),
}

lock = threading.Lock()

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


def safe_response(response):
    body = response.text[:12000]
    content_type = response.headers.get("Content-Type", "")

    markers = {
        "widgetStates": "widgetStates" in body,
        "tileGrid": "tileGrid" in body,
        "priceV2": "priceV2" in body,
        "captcha": "captcha" in body.lower(),
        "antibot": any(
            marker in body.lower()
            for marker in (
                "antibot",
                "anti-bot",
                "проверка браузера",
                "подтвердите, что вы не робот",
            )
        ),
        "challenge": "challenge" in body.lower(),
        "rr": "__rr=" in response.url,
    }

    result = {
        "status": response.status_code,
        "url_path": urlparse(response.url).path,
        "content_type": content_type,
        "body_length": len(response.content),
        "markers": markers,
    }

    if response.history:
        result["redirect_chain"] = [
            {
                "status": item.status_code,
                "path": urlparse(item.url).path,
            }
            for item in response.history
        ]

    if "json" in content_type.lower():
        try:
            data = response.json()

            if isinstance(data, dict):
                result["json_keys"] = list(data.keys())[:40]

                if isinstance(data.get("widgetStates"), dict):
                    widgets = data["widgetStates"]
                    result["widget_count"] = len(widgets)

                    sku_count = 0
                    price_count = 0

                    for value in widgets.values():
                        if not isinstance(value, str):
                            continue

                        if any(
                            marker in value
                            for marker in (
                                '"sku"',
                                '"productId"',
                                '"offer_id"',
                            )
                        ):
                            sku_count += 1

                        if "priceV2" in value:
                            price_count += 1

                    result["widgets_with_product_markers"] = sku_count
                    result["widgets_with_priceV2"] = price_count

                if "success" in data:
                    result["success"] = data["success"]

                if "error" in data:
                    result["has_error_field"] = True

        except Exception:
            result["json_parse_error"] = True

    else:
        result["body_preview"] = body[:350].replace("\n", " ")

    return result


def run_requests_test(name, url):
    session = requests.Session()
    session.headers.update(HEADERS)

    if PROXY_URL:
        session.proxies.update({
            "http": PROXY_URL,
            "https": PROXY_URL,
        })

    session.max_redirects = MAX_REDIRECTS

    result = {
        "client": "requests",
        "target": name,
        "started_at": now(),
    }

    try:
        for attempt in (1, 2):
            response = session.get(
                url,
                timeout=TIMEOUT,
                allow_redirects=True,
            )

            result[f"attempt_{attempt}"] = safe_response(response)
            response.close()

            if attempt == 1:
                time.sleep(1)

    except Exception as exc:
        result["error_type"] = type(exc).__name__

    finally:
        result["cookie_count"] = len(session.cookies)
        session.close()

    result["finished_at"] = now()
    return result


def run_curl_test(name, url):
    result = {
        "client": "curl_cffi",
        "target": name,
        "impersonate": "chrome",
        "started_at": now(),
    }

    session = None

    try:
        options = {
            "impersonate": "chrome",
            "headers": HEADERS,
            "timeout": TIMEOUT,
            "allow_redirects": True,
            "max_redirects": MAX_REDIRECTS,
        }

        if PROXY_URL:
            options["proxies"] = {
                "http": PROXY_URL,
                "https": PROXY_URL,
            }

        session = curl_requests.Session(**options)

        for attempt in (1, 2):
            response = session.get(url)

            result[f"attempt_{attempt}"] = safe_response(response)
            response.close()

            if attempt == 1:
                time.sleep(1)

    except Exception as exc:
        result["error_type"] = type(exc).__name__

    finally:
        if session is not None:
            try:
                result["cookie_count"] = len(session.cookies)
            except Exception:
                pass

            session.close()

    result["finished_at"] = now()
    return result


def run_tests():
    with lock:
        if state["running"]:
            return False

        state.update({
            "running": True,
            "started_at": now(),
            "finished_at": None,
            "status": "RUNNING",
            "results": [],
            "error": None,
        })

    results = []

    try:
        # Сначала сравниваем клиентов на главной странице.
        # Затем проверяем каталог.
        for name, url in TARGETS.items():
            for test_function in (
                run_requests_test,
                run_curl_test,
            ):
                result = test_function(name, url)
                results.append(result)

                with lock:
                    state["results"] = list(results)

                time.sleep(1)

        with lock:
            state["status"] = "COMPLETED"

    except Exception as exc:
        with lock:
            state["error"] = {
                "type": type(exc).__name__,
            }
            state["status"] = "ERROR"

    finally:
        with lock:
            state["running"] = False
            state["finished_at"] = now()

    return True


class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        body = json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            return self.send_json({
                "ok": True,
                "service": "glukolov-ozon-client-test",
                "time": now(),
            })

        if path == "/status":
            with lock:
                snapshot = json.loads(json.dumps(state))

            return self.send_json(snapshot)

        if path == "/test-http":
            with lock:
                already_running = state["running"]

            if already_running:
                return self.send_json({
                    "accepted": False,
                    "message": "Test already running",
                }, 409)

            threading.Thread(
                target=run_tests,
                daemon=True,
            ).start()

            return self.send_json({
                "accepted": True,
                "message": "Comparison started",
                "status_url": "/status",
            }, 202)

        return self.send_json({
            "error": "not_found",
            "available": [
                "/health",
                "/test-http",
                "/status",
            ],
        }, 404)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        Handler,
    )

    print(
        "Glukolov client comparison service started",
        flush=True,
    )

    server.serve_forever()
