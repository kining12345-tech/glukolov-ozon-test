import os
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from curl_cffi import requests as curl_requests


PORT = int(os.getenv("PORT", "8080"))
TIMEOUT = 20

PROXY_URL = os.getenv("PROXY_URL", "").strip()

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


def proxy_configured():
    return bool(PROXY_URL)


def get_proxies():
    if not PROXY_URL:
        return None

    return {
        "http": PROXY_URL,
        "https": PROXY_URL,
    }


def safe_url(url):
    from urllib.parse import urlsplit

    parsed = urlsplit(url)

    # Не возвращаем логин, пароль и query-параметры.
    return f"{parsed.scheme}://{parsed.hostname or ''}"


def run_requests_test(name, url, use_proxy):
    result = {
        "test": name,
        "client": "requests",
        "proxy_enabled": use_proxy,
        "started_at": now(),
    }

    session = requests.Session()

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    })

    if use_proxy:
        session.proxies.update(get_proxies() or {})

    try:
        response = session.get(
            url,
            timeout=TIMEOUT,
            allow_redirects=True,
        )

        result.update({
            "status_code": response.status_code,
            "final_url": safe_url(response.url),
            "body_length": len(response.content),
            "content_type": response.headers.get("Content-Type"),
        })

        if "ipify" in url:
            try:
                data = response.json()
                result["external_ip"] = data.get("ip")
            except Exception:
                result["ip_response_parse_failed"] = True

        if "ozon.ru" in url:
            body = response.text[:12000].lower()

            result["markers"] = {
                "widgetStates": "widgetstates" in body,
                "tileGrid": "tilegrid" in body,
                "priceV2": "pricev2" in body,
                "challenge": "challengeurl" in body,
                "incidentId": "incidentid" in body,
            }

    except Exception as exc:
        result["error_type"] = type(exc).__name__

    finally:
        session.close()

    result["finished_at"] = now()
    return result


def run_curl_test(name, url):
    result = {
        "test": name,
        "client": "curl_cffi",
        "impersonate": "chrome",
        "proxy_enabled": True,
        "started_at": now(),
    }

    session = None

    try:
        session = curl_requests.Session(
            impersonate="chrome",
            proxies=get_proxies(),
            timeout=TIMEOUT,
        )

        response = session.get(
            url,
            allow_redirects=True,
        )

        result.update({
            "status_code": response.status_code,
            "final_url": safe_url(response.url),
            "body_length": len(response.content),
            "content_type": response.headers.get("Content-Type"),
        })

        if "ipify" in url:
            try:
                result["external_ip"] = response.json().get("ip")
            except Exception:
                result["ip_response_parse_failed"] = True

        if "ozon.ru" in url:
            body = response.text[:12000].lower()

            result["markers"] = {
                "widgetStates": "widgetstates" in body,
                "tileGrid": "tilegrid" in body,
                "priceV2": "pricev2" in body,
                "challenge": "challengeurl" in body,
                "incidentId": "incidentid" in body,
            }

    except Exception as exc:
        result["error_type"] = type(exc).__name__

    finally:
        if session:
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

    tests = [
        (
            "IP without proxy",
            lambda: run_requests_test(
                "IP without proxy",
                "https://api.ipify.org?format=json",
                False,
            ),
        ),
        (
            "IP through proxy",
            lambda: run_requests_test(
                "IP through proxy",
                "https://api.ipify.org?format=json",
                True,
            ),
        ),
        (
            "Ozon without proxy",
            lambda: run_requests_test(
                "Ozon without proxy",
                "https://www.ozon.ru/",
                False,
            ),
        ),
        (
            "Ozon through proxy",
            lambda: run_requests_test(
                "Ozon through proxy",
                "https://www.ozon.ru/",
                True,
            ),
        ),
        (
            "Ozon curl_cffi through proxy",
            lambda: run_curl_test(
                "Ozon curl_cffi through proxy",
                "https://www.ozon.ru/",
            ),
        ),
    ]

    try:
        for _, test in tests:
            result = test()

            results.append(result)

            with lock:
                state["results"] = list(results)

        with lock:
            state["status"] = "COMPLETED"

    except Exception as exc:
        with lock:
            state["status"] = "ERROR"
            state["error"] = {
                "type": type(exc).__name__,
            }

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
        from urllib.parse import urlsplit

        path = urlsplit(self.path).path

        if path == "/health":
            return self.send_json({
                "ok": True,
                "service": "glukolov-proxy-diagnostic",
                "proxy_configured": proxy_configured(),
                "time": now(),
            })

        if path == "/status":
            with lock:
                snapshot = json.loads(json.dumps(state))

            return self.send_json(snapshot)

        if path == "/test-http":
            with lock:
                if state["running"]:
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
                "message": "Proxy diagnostic started",
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
        "Glukolov proxy diagnostic started",
        flush=True,
    )

    server.serve_forever()
