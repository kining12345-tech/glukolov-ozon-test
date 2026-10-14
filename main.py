import os
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

import requests


PORT = int(os.environ.get("PORT", "8000"))

PROXY_HOST = os.environ.get("PROXY_HOST", "").strip()
PROXY_PORT = os.environ.get("PROXY_PORT", "").strip()
PROXY_USER = os.environ.get("PROXY_USER", "").strip()
PROXY_PASS = os.environ.get("PROXY_PASS", "")

state = {
    "running": False,
    "result": None,
    "last_error": None,
}

lock = threading.Lock()

USER_AGENT = (
    "Mozilla/5.0 (Android 13; Mobile; rv:157.0) "
    "Gecko/157.0 Firefox/157.0"
)

TESTS = {
    "ipify": "https://api.ipify.org/?format=json",
    "httpbin": "https://httpbin.org/get",
    "ozon": "https://www.ozon.ru/",
}


def build_proxy_url():
    if not PROXY_HOST or not PROXY_PORT:
        raise RuntimeError(
            "Не заданы PROXY_HOST или PROXY_PORT в переменных Hostless"
        )

    if not PROXY_USER or not PROXY_PASS:
        raise RuntimeError(
            "Не заданы PROXY_USER или PROXY_PASS в переменных Hostless"
        )

    user = quote(PROXY_USER, safe="")
    password = quote(PROXY_PASS, safe="")

    return f"http://{user}:{password}@{PROXY_HOST}:{PROXY_PORT}"


def run_tests():
    started = datetime.now(timezone.utc).isoformat()

    result = {
        "project": "Glukolov Ozon Russian proxy test",
        "started_at": started,
        "proxy_configured": False,
        "tests": {},
    }

    try:
        proxy_url = build_proxy_url()

        # Не записываем proxy_url в результат: в нём есть учётные данные.
        proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }

        result["proxy_configured"] = True

        session = requests.Session()
        session.trust_env = False
        session.proxies.update(proxies)
        session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/json,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "ru-RU",
        })

        for name, url in TESTS.items():
            item = {
                "url": url,
                "through_proxy": True,
            }

            try:
                response = session.get(
                    url,
                    timeout=(15, 30),
                    allow_redirects=True,
                )

                body = response.text
                lower = body.lower()

                item.update({
                    "status": response.status_code,
                    "final_url": response.url,
                    "content_type": response.headers.get(
                        "Content-Type", ""
                    ),
                    "length": len(response.content),
                    "markers": {
                        "challenge": (
                            "challenge" in lower
                            or "проверяем, что вы не робот" in lower
                        ),
                        "antibot": (
                            "antibot" in lower
                            or "anti-bot" in lower
                        ),
                        "connection_error": (
                            "похоже, нет соединения" in lower
                            or "connection_error" in lower
                        ),
                        "product": (
                            "tilegrid" in lower
                            or "pricev2" in lower
                            or "product-card" in lower
                        ),
                    },
                    "body_start": body[:1200],
                })

                if name == "ipify":
                    try:
                        item["observed_ip"] = response.json().get("ip")
                    except Exception:
                        item["observed_ip"] = None

                if name == "httpbin":
                    try:
                        data = response.json()
                        item["httpbin_origin"] = data.get("origin")
                    except Exception:
                        item["httpbin_origin"] = None

            except Exception as exc:
                # Не включаем URL прокси или его учётные данные в ошибки.
                item["error_type"] = type(exc).__name__
                item["error"] = str(exc)[:500]

            result["tests"][name] = item

        session.close()

    except Exception as exc:
        result["configuration_error"] = type(exc).__name__
        result["message"] = str(exc)[:500]

    with lock:
        state["result"] = result
        state["running"] = False


def start_test():
    with lock:
        if state["running"]:
            return False

        state["running"] = True
        state["last_error"] = None

    thread = threading.Thread(target=run_tests, daemon=True)
    thread.start()
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
        path = self.path.split("?", 1)[0].rstrip("/")

        if path in ("", "/health"):
            self.send_json({
                "ok": True,
                "project": "Glukolov Ozon Russian proxy test",
                "proxy_variables_present": {
                    "PROXY_HOST": bool(PROXY_HOST),
                    "PROXY_PORT": bool(PROXY_PORT),
                    "PROXY_USER": bool(PROXY_USER),
                    "PROXY_PASS": bool(PROXY_PASS),
                },
            })
            return

        if path == "/test":
            started = start_test()

            with lock:
                current = state["running"]

            self.send_json({
                "accepted": started,
                "running": current,
                "message": (
                    "Тест запущен. Откройте /status через несколько секунд."
                    if started
                    else "Тест уже выполняется."
                ),
                "status_url": "/status",
            }, 202)
            return

        if path == "/status":
            with lock:
                snapshot = {
                    "running": state["running"],
                    "result": state["result"],
                    "last_error": state["last_error"],
                }

            self.send_json(snapshot)
            return

        self.send_json({
            "error": "Not found",
            "endpoints": ["/health", "/test", "/status"],
        }, 404)

    def log_message(self, fmt, *args):
        # Не записываем заголовки запросов и другие потенциальные секреты.
        print(
            f"{self.client_address[0]} - {self.log_date_time_string()} "
            f"{fmt % args}"
        )


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Glukolov proxy test listening on port {PORT}", flush=True)
    server.serve_forever()
