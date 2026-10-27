import os
import json
import time
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


PORT = int(os.getenv("PORT", "8080"))
TIMEOUT_MS = 45000

PROXY_URL = os.getenv("PROXY_URL", "")

TARGETS = {
    "home": "https://www.ozon.ru/",
    "catalog": (
        "https://www.ozon.ru/category/"
        "bluzy-i-rubashki-zhenskie-7511/"
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


def inspect_page(page, response, name):
    try:
        title = page.title()
    except Exception:
        title = None

    try:
        text = page.locator("body").inner_text(timeout=10000)
        text = text[:2000]
    except Exception:
        text = ""

    try:
        cookies = page.context.cookies()
        cookie_count = len(cookies)
    except Exception:
        cookie_count = None

    try:
        metrics = page.evaluate("""() => ({
            readyState: document.readyState,
            htmlLength: document.documentElement
                ? document.documentElement.innerHTML.length : 0,
            scripts: document.scripts.length,
            links: document.links.length
        })""")
    except Exception:
        metrics = {}

    html_lower = text.lower()

    return {
        "target": name,
        "http_status": response.status if response else None,
        "final_path": urlparse(page.url).path,
        "title": title,
        "body_length": len(text),
        "body_preview": text[:500],
        "cookie_count": cookie_count,
        "metrics": metrics,
        "markers": {
            "captcha": "captcha" in html_lower,
            "challenge": "challenge" in html_lower,
            "blocked": any(x in html_lower for x in (
                "доступ ограничен",
                "доступ запрещён",
                "access denied",
                "request blocked",
            )),
            "product_words": any(x in html_lower for x in (
                "товары",
                "отзывы",
                "купить",
            )),
            "price_marker": "price" in html_lower,
        },
    }


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
        with sync_playwright() as p:
            launch_options = {
                "headless": True,
            }

            if PROXY_URL:
                launch_options["proxy"] = {
                    "server": PROXY_URL,
                }

            browser = p.chromium.launch(**launch_options)

            context = browser.new_context(
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                viewport={
                    "width": 1365,
                    "height": 900,
                },
            )

            page = context.new_page()
            page.set_default_timeout(TIMEOUT_MS)

            for name, url in TARGETS.items():
                result = {
                    "target": name,
                    "started_at": now(),
                }

                try:
                    response = page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=TIMEOUT_MS,
                    )

                    result.update(
                        inspect_page(page, response, name)
                    )

                    # Небольшое ожидание для завершения
                    # обычной загрузки клиентских компонентов.
                    page.wait_for_timeout(3000)

                    result["after_wait"] = inspect_page(
                        page,
                        response,
                        name,
                    )

                except Exception as exc:
                    result["error_type"] = type(exc).__name__
                    result["error_message"] = str(exc)[:300]
                    result["current_path"] = urlparse(page.url).path

                result["finished_at"] = now()
                results.append(result)

                with lock:
                    state["results"] = list(results)

                time.sleep(2)

            context.close()
            browser.close()

        with lock:
            state["status"] = "COMPLETED"

    except Exception as exc:
        with lock:
            state["error"] = {
                "type": type(exc).__name__,
                "message": str(exc)[:500],
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
                "service": "glukolov-playwright-test",
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
                "message": "Playwright test started",
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
        "Glukolov Playwright diagnostic started",
        flush=True,
    )

    server.serve_forever()
