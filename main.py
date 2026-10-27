import os
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlsplit

import requests
from curl_cffi import requests as curl_requests


PORT = int(os.getenv("PORT", "8080"))
TIMEOUT = 15

# Настройки прокси
PROXY_HOST = os.getenv("PROXY_HOST", "").strip()
PROXY_PORT = os.getenv("PROXY_PORT", "").strip()
PROXY_USER = os.getenv("PROXY_USER", "")
PROXY_PASS = os.getenv("PROXY_PASS", "")
PROXY_URL_ENV = os.getenv("PROXY_URL", "").strip()

lock = threading.Lock()

state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "status": "IDLE",
    "proxy": {},
    "results": [],
    "error": None,
}


def now():
    return datetime.now(timezone.utc).isoformat()


def build_proxy_url():
    """
    Приоритет:
    1. PROXY_URL, если задан.
    2. PROXY_HOST + PROXY_PORT + учётные данные.
    """
    if PROXY_URL_ENV:
        return PROXY_URL_ENV

    if not PROXY_HOST or not PROXY_PORT:
        return ""

    # Кодируем специальные символы в логине и пароле.
    auth = ""

    if PROXY_USER:
        auth = quote(PROXY_USER, safe="")

        if PROXY_PASS:
            auth += ":" + quote(PROXY_PASS, safe="")

        auth += "@"

    # Если схема не указана, используем HTTP-прокси.
    return f"http://{auth}{PROXY_HOST}:{PROXY_PORT}"


PROXY_URL = build_proxy_url()


def proxy_mapping():
    if not PROXY_URL:
        return None

    return {
        "http": PROXY_URL,
        "https": PROXY_URL,
    }


def proxy_info():
    if not PROXY_URL:
        return {
            "configured": False,
            "reason": "Proxy URL not configured",
        }

    try:
        parsed = urlsplit(PROXY_URL)

        return {
            "configured": True,
            "host": parsed.hostname,
            "port": parsed.port,
            "scheme": parsed.scheme,
            "authentication_configured": bool(
                parsed.username
            ),
            "credentials_hidden": True,
        }

    except Exception:
        return {
            "configured": True,
            "parse_error": True,
            "credentials_hidden": True,
        }


def get_ip(session, label):
    result = {
        "test": label,
        "started_at": now(),
    }

    try:
        response = session.get(
            "https://api.ipify.org?format=json",
            timeout=TIMEOUT,
        )

        result["status_code"] = response.status_code

        if response.ok:
            result["external_ip"] = response.json().get("ip")

    except Exception as exc:
        result["error_type"] = type(exc).__name__

    result["finished_at"] = now()
    return result


def test_ozon(session, label):
    result = {
        "test": label,
        "started_at": now(),
    }

    try:
        response = session.get(
            "https://www.ozon.ru/",
            timeout=TIMEOUT,
            allow_redirects=True,
        )

        result.update({
            "status_code": response.status_code,
            "body_length": len(response.content),
            "content_type": response.headers.get(
                "Content-Type"
            ),
        })

        body = response.text[:20000].lower()

        result["markers"] = {
            "widgetStates": "widgetstates" in body,
            "tileGrid": "tilegrid" in body,
            "priceV2": "pricev2" in body,
            "captcha": "captcha" in body,
            "challenge": "challenge" in body,
            "incidentId": "incidentid" in body,
        }

    except Exception as exc:
        result["error_type"] = type(exc).__name__

    result["finished_at"] = now()
    return result


def direct_requests_session():
    session = requests.Session()
    session.trust_env = False
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
    })
    return session


def proxy_requests_session():
    session = requests.Session()
    session.trust_env = False
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
    })

    proxies = proxy_mapping()

    if proxies:
        session.proxies.update(proxies)

    return session


def run_tests():
    with lock:
        if state["running"]:
            return False

        state.update({
            "running": True,
            "started_at": now(),
            "finished_at": None,
            "status": "RUNNING",
            "proxy": proxy_info(),
            "results": [],
            "error": None,
        })

    results = []

    def save(result):
        results.append(result)

        with lock:
            state["results"] = list(results)

    try:
        # 1. Внешний IP без прокси
        direct = direct_requests_session()

        try:
            save(get_ip(direct, "IP without proxy"))
            save(test_ozon(direct, "Ozon without proxy"))
        finally:
            direct.close()

        # 2. Внешний IP через настроенный прокси
        if not PROXY_URL:
            save({
                "test": "Proxy tests",
                "error": "Proxy is not configured",
            })
        else:
            proxied = proxy_requests_session()

            try:
                save(get_ip(proxied, "IP through proxy"))
                save(test_ozon(proxied, "Ozon through proxy"))
            finally:
                proxied.close()

            # 3. Повтор через curl_cffi с прокси
            curl_session = None

            try:
                curl_session = curl_requests.Session(
                    impersonate="chrome",
                    proxies=proxy_mapping(),
                    timeout=TIMEOUT,
                )

                save(get_ip(
                    curl_session,
                    "IP through proxy using curl_cffi",
                ))

                save(test_ozon(
                    curl_session,
                    "Ozon through proxy using curl_cffi",
                ))

            except Exception as exc:
                save({
                    "test": "curl_cffi session",
                    "error_type": type(exc).__name__,
                })

            finally:
                if curl_session:
                    curl_session.close()

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
                "proxy": proxy_info(),
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
    print(
        "Glukolov proxy diagnostic started",
        flush=True,
    )

    print(
        "Proxy configured:",
        bool(PROXY_URL),
        flush=True,
    )

    # Не выводим PROXY_URL: он содержит пароль.
    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        Handler,
    )

    server.serve_forever()
