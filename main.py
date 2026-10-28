import os
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

from playwright.sync_api import sync_playwright


PORT = int(os.getenv("PORT", "8080"))
TIMEOUT = 45000

PROXY_HOST = os.getenv("PROXY_HOST", "").strip()
PROXY_PORT = os.getenv("PROXY_PORT", "").strip()
PROXY_USER = os.getenv("PROXY_USER", "")
PROXY_PASS = os.getenv("PROXY_PASS", "")
PROXY_URL = os.getenv("PROXY_URL", "").strip()

lock = threading.Lock()

state = {
    "running": False,
    "status": "IDLE",
    "started_at": None,
    "finished_at": None,
    "proxy": {},
    "results": [],
    "error": None,
}


def now():
    return datetime.now(timezone.utc).isoformat()


def get_proxy():
    if PROXY_URL:
        return {"server": PROXY_URL}

    if not PROXY_HOST or not PROXY_PORT:
        return None

    proxy = {
        "server": f"http://{PROXY_HOST}:{PROXY_PORT}"
    }

    if PROXY_USER:
        proxy["username"] = PROXY_USER

    if PROXY_PASS:
        proxy["password"] = PROXY_PASS

    return proxy


def proxy_info():
    proxy = get_proxy()

    if not proxy:
        return {"configured": False}

    return {
        "configured": True,
        "host": PROXY_HOST or "configured in PROXY_URL",
        "port": PROXY_PORT or None,
        "authentication_configured": bool(
            PROXY_USER and PROXY_PASS
        ),
        "credentials_hidden": True,
    }


def inspect_page(page, target, url):
    result = {
        "target": target,
        "started_at": now(),
    }

    try:
        response = page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=TIMEOUT,
        )

        result["http_status"] = (
            response.status if response else None
        )
        result["final_url"] = page.url
        result["title"] = page.title()

        data = page.evaluate("""() => {
            const body = document.body?.innerText || '';
            const html = document.documentElement?.outerHTML || '';

            return {
                body_length: body.length,
                body_preview: body.slice(0, 700),
                html_length: html.length,
                product_links: document.querySelectorAll(
                    'a[href*="/product/"]'
                ).length,
                price_markers: (
                    html.includes('priceV2') ||
                    html.includes('price_v2')
                ),
                widget_states: html.includes('widgetStates'),
                tile_grid: html.includes('tileGrid'),
                challenge_text: (
                    /challenge|captcha|похоже, нет соединения/i
                ).test(body),
                product_words: (
                    /добавить в корзину|купить|цена/i
                ).test(body)
            };
        }""")

        result.update(data)

    except Exception as exc:
        # Не записываем текст исключения целиком:
        # он потенциально может содержать данные подключения.
        result["error_type"] = type(exc).__name__

    result["finished_at"] = now()
    return result


def run_tests():
    with lock:
        if state["running"]:
            return

        state.update({
            "running": True,
            "status": "RUNNING",
            "started_at": now(),
            "finished_at": None,
            "proxy": proxy_info(),
            "results": [],
            "error": None,
        })

    results = []
    browser = None

    try:
        proxy = get_proxy()

        if not proxy:
            raise RuntimeError("Proxy is not configured")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                proxy=proxy,
                timeout=TIMEOUT,
            )

            context = browser.new_context(
                viewport={"width": 1365, "height": 900},
                locale="ru-RU",
                timezone_id="Europe/Moscow",
            )

            page = context.new_page()
            page.set_default_timeout(15000)

            tests = [
                (
                    "IP through Playwright proxy",
                    "https://api.ipify.org?format=json",
                ),
                (
                    "Ozon homepage through Playwright proxy",
                    "https://www.ozon.ru/",
                ),
                (
                    "Ozon category through Playwright proxy",
                    "https://www.ozon.ru/category/bluzy-i-rubashki-zhenskie-7511/",
                ),
            ]

            for name, url in tests:
                result = inspect_page(page, name, url)

                if "ipify.org" in url and "body_preview" in result:
                    try:
                        parsed = json.loads(
                            result["body_preview"]
                        )
                        result["external_ip"] = parsed.get("ip")
                    except Exception:
                        pass

                results.append(result)

                with lock:
                    state["results"] = list(results)

            context.close()
            browser.close()
            browser = None

        with lock:
            state["status"] = "COMPLETED"

    except Exception as exc:
        with lock:
            state["status"] = "ERROR"
            state["error"] = {
                "type": type(exc).__name__,
            }

    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass

        with lock:
            state["running"] = False
            state["finished_at"] = now()


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
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        from urllib.parse import urlsplit

        path = urlsplit(self.path).path

        if path == "/health":
            return self.send_json({
                "ok": True,
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
                "message": "Playwright proxy test started",
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
    print("Playwright proxy diagnostic started", flush=True)
    server.serve_forever()
