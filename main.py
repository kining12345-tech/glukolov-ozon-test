import os
import re
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

from playwright.sync_api import sync_playwright


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

TESTS = {
    "ipify": "https://api.ipify.org/?format=json",
    "httpbin": "https://httpbin.org/get",
    "ozon": "https://www.ozon.ru/",
}

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def redact(text):
    """Удаляет токены из диагностического текста."""
    text = re.sub(
        r'(?i)("token"\s*:\s*")[^"]*(")',
        r'\1[REDACTED]\2',
        text,
    )
    text = re.sub(
        r'(?i)(token=)[^&"\s]+',
        r'\1[REDACTED]',
        text,
    )
    text = re.sub(
        r'(?i)(authorization:\s*bearer\s+)[^\s]+',
        r'\1[REDACTED]',
        text,
    )
    return text


def check_proxy_config():
    missing = []

    for name, value in (
        ("PROXY_HOST", PROXY_HOST),
        ("PROXY_PORT", PROXY_PORT),
        ("PROXY_USER", PROXY_USER),
        ("PROXY_PASS", PROXY_PASS),
    ):
        if not value:
            missing.append(name)

    if missing:
        raise RuntimeError(
            "Не заданы переменные прокси: " + ", ".join(missing)
        )


def run_tests():
    result = {
        "project": "Glukolov Chromium Russian proxy test",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "browser": "Chromium",
        "proxy_configured": False,
        "tests": {},
        "steps": [],
    }

    browser = None

    try:
        check_proxy_config()
        result["proxy_configured"] = True
        result["steps"].append("PROXY_CONFIG_OK")

        with sync_playwright() as p:
            result["steps"].append("PLAYWRIGHT_READY")

            browser = p.chromium.launch(
                headless=True,
                proxy={
                    "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
                    "username": PROXY_USER,
                    "password": PROXY_PASS,
                },
                timeout=30000,
            )

            result["steps"].append("CHROMIUM_STARTED")

            context = browser.new_context(
                user_agent=USER_AGENT,
                locale="ru-RU",
                viewport={"width": 1365, "height": 900},
                ignore_https_errors=False,
            )

            page = context.new_page()
            page.set_default_navigation_timeout(30000)
            page.set_default_timeout(10000)

            for name, url in TESTS.items():
                item = {
                    "url": url,
                    "browser": "Chromium",
                    "through_proxy": True,
                }

                response_status = None
                failed_requests = []

                def on_response(response):
                    nonlocal response_status

                    if response.request.is_navigation_request():
                        if response.url == url or name == "ozon":
                            response_status = response.status

                def on_failed_request(request):
                    if len(failed_requests) < 10:
                        failed_requests.append({
                            "url": redact(request.url[:300]),
                            "failure": redact(
                                str(request.failure or "")[:300]
                            ),
                        })

                page.on("response", on_response)
                page.on("requestfailed", on_failed_request)

                try:
                    response = page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )

                    if response is not None:
                        response_status = response.status

                    # Даем странице короткое время на первичную отрисовку.
                    page.wait_for_timeout(1500)

                    current_url = page.url
                    title = page.title()

                    try:
                        body = page.locator("body").inner_text(
                            timeout=5000
                        )
                    except Exception:
                        body = ""

                    try:
                        html = page.content()
                    except Exception:
                        html = ""

                    combined = (html + "\n" + body).lower()

                    item.update({
                        "status": response_status,
                        "final_url": redact(current_url[:1000]),
                        "title": redact(title[:300]),
                        "html_length": len(html),
                        "body_length": len(body),
                        "markers": {
                            "challenge": (
                                "challenge" in combined
                                or "проверяем, что вы не робот" in combined
                            ),
                            "antibot": (
                                "antibot" in combined
                                or "anti-bot" in combined
                                or "antibot challenge" in combined
                            ),
                            "block_page": (
                                "block.html" in combined
                                or "incidentid" in combined
                            ),
                            "connection_error": (
                                "похоже, нет соединения" in combined
                                or "connection_error" in combined
                            ),
                            "product_data": (
                                "tilegrid" in combined
                                or "pricev2" in combined
                            ),
                            "product_cards": (
                                "product-card" in combined
                                or "widget-search-result-container" in combined
                            ),
                        },
                        "body_start": redact(body[:1000]),
                        "html_start": redact(html[:1000]),
                    })

                    if name == "ipify":
                        match = re.search(
                            r'"ip"\s*:\s*"([^"]+)"',
                            body,
                        )
                        item["observed_ip"] = (
                            match.group(1) if match else None
                        )

                    if name == "httpbin":
                        match = re.search(
                            r'"origin"\s*:\s*"([^"]+)"',
                            body,
                        )
                        item["httpbin_origin"] = (
                            match.group(1) if match else None
                        )

                except Exception as exc:
                    item.update({
                        "status": response_status,
                        "error_type": type(exc).__name__,
                        "error": redact(str(exc)[:700]),
                        "current_url": redact(page.url[:1000]),
                    })

                finally:
                    page.remove_listener("response", on_response)
                    page.remove_listener(
                        "requestfailed", on_failed_request
                    )

                if failed_requests:
                    item["failed_requests"] = failed_requests

                result["tests"][name] = item
                result["steps"].append(f"TEST_{name.upper()}_DONE")

                # После ответа Ozon не выполняем дополнительные запросы к нему.
                # Переходим только к следующей независимой проверке.

            context.close()
            browser.close()
            browser = None

            result["steps"].append("BROWSER_CLOSED")
            result["completed_at"] = datetime.now(
                timezone.utc
            ).isoformat()

    except Exception as exc:
        result["fatal_error"] = {
            "type": type(exc).__name__,
            "message": redact(str(exc)[:1000]),
        }

        result["completed_at"] = datetime.now(
            timezone.utc
        ).isoformat()

        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass

    finally:
        with lock:
            state["result"] = result
            state["running"] = False


def start_test():
    with lock:
        if state["running"]:
            return False

        state["running"] = True
        state["last_error"] = None

    threading.Thread(target=run_tests, daemon=True).start()
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
        path = self.path.split("?", 1)[0].rstrip("/")

        if path in ("", "/health"):
            self.send_json({
                "ok": True,
                "project": "Glukolov Chromium Russian proxy test",
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
                running = state["running"]

            self.send_json({
                "accepted": started,
                "running": running,
                "message": (
                    "Тест запущен. Проверьте /status через 10–15 секунд."
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
        print(
            f"{self.client_address[0]} "
            f"{self.log_date_time_string()} {fmt % args}",
            flush=True,
        )


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(
        f"Glukolov Chromium proxy test listening on port {PORT}",
        flush=True,
    )
    server.serve_forever()
