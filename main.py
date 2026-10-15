import os
import json
import time
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin, urlparse

import requests


PORT = int(os.getenv("PORT", "8080"))
TIMEOUT = 12
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
    "Connection": "close",
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


def make_session():
    session = requests.Session()
    session.headers.update(HEADERS)

    if PROXY_URL:
        session.proxies.update({
            "http": PROXY_URL,
            "https": PROXY_URL,
        })

    # requests.Session хранит лимит перенаправлений здесь,
    # а не в аргументах Session.request().
    session.max_redirects = MAX_REDIRECTS

    return session


def cookie_metadata(session):
    result = []

    for cookie in session.cookies:
        result.append({
            "name": cookie.name,
            "domain": cookie.domain,
            "path": cookie.path,
            "secure": bool(cookie.secure),
            "expires": cookie.expires,
        })

    return result


def safe_response(response):
    body = response.text[:5000]

    markers = {
        "rr": "__rr=" in response.url
              or "__rr=" in response.headers.get("Location", ""),
        "widget_states": "widgetStates" in body,
        "tile_grid": "tileGrid" in body,
        "price_v2": "priceV2" in body,
        "captcha": "captcha" in body.lower(),
        "challenge": "challenge" in body.lower(),
    }

    result = {
        "status": response.status_code,
        "url_path": urlparse(response.url).path,
        "location": None,
        "headers": {
            "Server": response.headers.get("Server"),
            "Content-Type": response.headers.get("Content-Type"),
            "Content-Length": response.headers.get("Content-Length"),
        },
        "body_length": len(response.content),
        "markers": markers,
    }

    location = response.headers.get("Location")

    if location:
        absolute_location = urljoin(response.url, location)
        parsed = urlparse(absolute_location)

        # Не показываем токены и прочие параметры поддержки.
        result["location"] = (
            parsed.scheme + "://" + parsed.netloc
            + parsed.path
            + (("?" + parsed.query[:300]) if parsed.query else "")
        )

    content_type = response.headers.get("Content-Type", "")

    if "json" in content_type.lower():
        try:
            data = response.json()

            # Выводим только безопасные диагностические поля.
            if isinstance(data, dict):
                if "incidentId" in data:
                    result["incidentId"] = data["incidentId"]

                if "success" in data:
                    result["success"] = data["success"]

                if "data" in data:
                    result["has_data"] = data["data"] is not None

                result["json_keys"] = list(data.keys())[:30]
        except Exception:
            pass
    else:
        result["body_preview"] = body[:250]

    return result


def one_request(session, url):
    response = session.get(
        url,
        timeout=TIMEOUT,
        allow_redirects=False,
    )

    result = safe_response(response)
    response.close()

    return result


def trace_redirects(url):
    """
    Вручную проходим цепочку перенаправлений.
    На каждом шаге сохраняется одна и та же cookie-сессия.
    Токены и значения cookies в результат не попадают.
    """
    session = make_session()
    steps = []
    current_url = url
    error = None

    try:
        for number in range(MAX_REDIRECTS + 1):
            response = session.get(
                current_url,
                timeout=TIMEOUT,
                allow_redirects=False,
            )

            step = safe_response(response)
            step["step"] = number + 1
            steps.append(step)

            location = response.headers.get("Location")
            status_code = response.status_code
            response.close()

            if status_code not in (301, 302, 303, 307, 308):
                break

            if not location:
                break

            if number >= MAX_REDIRECTS:
                error = "redirect_limit_reached"
                break

            current_url = urljoin(current_url, location)

    except requests.RequestException as exc:
        error = type(exc).__name__

    finally:
        cookies = cookie_metadata(session)
        session.close()

    return {
        "scenario": "manual_redirect_trace",
        "initial_url_path": urlparse(url).path,
        "steps": steps,
        "step_count": len(steps),
        "cookies_at_end": cookies,
        "error": error,
    }


def test_target(name, url):
    output = {
        "target": name,
        "started_at": now(),
    }

    # Тест A: первый запрос в новой сессии.
    session_a = make_session()

    try:
        output["first_request"] = one_request(session_a, url)
        output["cookies_after_first"] = cookie_metadata(session_a)

        # Тест B: повторный запрос с сохранёнными cookies.
        output["second_request_same_session"] = one_request(
            session_a, url
        )
        output["cookies_after_second"] = cookie_metadata(session_a)

    except Exception as exc:
        output["session_test_error"] = type(exc).__name__

    finally:
        session_a.close()

    # Тест C: ручное прохождение всей цепочки 307/других редиректов.
    output["redirect_trace"] = trace_redirects(url)
    output["finished_at"] = now()

    return output


def run_tests():
    with lock:
        if state["running"]:
            return False

        state["running"] = True
        state["started_at"] = now()
        state["finished_at"] = None
        state["status"] = "RUNNING"
        state["results"] = []
        state["error"] = None

    try:
        results = []

        for name, url in TARGETS.items():
            results.append(test_target(name, url))

            with lock:
                state["results"] = list(results)

            # Небольшая пауза между проверками.
            time.sleep(1)

        with lock:
            state["results"] = results
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
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            return self.send_json({
                "ok": True,
                "service": "glukolov-ozon-test",
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

            thread = threading.Thread(
                target=run_tests,
                daemon=True,
            )
            thread.start()

            return self.send_json({
                "accepted": True,
                "message": "Tests started",
                "status_url": "/status",
            }, 202)

        return self.send_json({
            "error": "not_found",
            "available": ["/health", "/test-http", "/status"],
        }, 404)

    def log_message(self, format, *args):
        # Не выводим URL и заголовки запросов в журнал.
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("Glukolov HTTP diagnostic service started", flush=True)
    server.serve_forever()
