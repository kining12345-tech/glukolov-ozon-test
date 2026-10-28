import os
import json
import threading
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


PORT = int(os.getenv("PORT", "8080"))
TIMEOUT = 20000

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
    "browser": "firefox",
    "stage": None,
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


def set_stage(stage):
    with lock:
        state["stage"] = stage


def add_result(result):
    with lock:
        state["results"].append(result)


def inspect_page(page, target, url):
    result = {
        "target": target,
        "url": url,
        "started_at": now(),
        "http_status": None,
        "final_url": None,
        "error": None,
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

        try:
            result["title"] = page.title()
        except Exception as exc:
            result["title_error"] = str(exc)

        try:
            data = page.evaluate("""() => {
                const body = document.body?.innerText || '';
                const html = document.documentElement?.outerHTML || '';

                return {
                    body_length: body.length,
                    body_preview: body.slice(0, 500),
                    html_length: html.length,
                    product_links: document.querySelectorAll(
                        'a[href*="/product/"]'
                    ).length,
                    price_markers:
                        html.includes('priceV2') ||
                        html.includes('price_v2'),
                    widget_states: html.includes('widgetStates'),
                    tile_grid: html.includes('tileGrid'),
                    challenge_text:
                        /captcha|похоже, нет соединения|выключите vpn/i.test(body),
                    product_words:
                        /добавить в корзину|купить|цена/i.test(body)
                };
            }""")

            result.update(data)

        except Exception as exc:
            result["inspection_error"] = {
                "type": type(exc).__name__,
                "message": str(exc)[:1000],
            }

        if "ipify.org" in url and result.get("body_preview"):
            try:
                result["external_ip"] = json.loads(
                    result["body_preview"]
                ).get("ip")
            except Exception:
                result["ip_parse_error"] = (
                    result["body_preview"][:200]
                )

    except Exception as exc:
        result["error"] = {
            "type": type(exc).__name__,
            "message": str(exc)[:2000],
        }

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
            "browser": "firefox",
            "stage": "initializing",
            "proxy": proxy_info(),
            "results": [],
            "error": None,
        })

    browser = None
    context = None

    try:
        set_stage("checking_proxy_configuration")
        proxy = get_proxy()

        if not proxy:
            raise RuntimeError(
                "Proxy is not configured. "
                "Check PROXY_HOST, PROXY_PORT or PROXY_URL."
            )

        set_stage("starting_playwright")

        with sync_playwright() as p:
            set_stage("launching_firefox")

            browser = p.firefox.launch(
                headless=True,
                proxy=proxy,
                timeout=TIMEOUT,
            )

            set_stage("creating_browser_context")

            context = browser.new_context(
                viewport={"width": 900, "height": 700},
                locale="ru-RU",
                timezone_id="Europe/Moscow",
            )

            set_stage("creating_page")
            page = context.new_page()
            page.set_default_timeout(10000)

            tests = [
                (
                    "Firefox external IP through proxy",
                    "https://api.ipify.org?format=json",
                ),
                (
                    "Firefox Ozon homepage through proxy",
                    "https://www.ozon.ru/",
                ),
            ]

            for name, url in tests:
                set_stage("testing: " + name)

                result = inspect_page(page, name, url)
                add_result(result)

                # Не повторяем запрос к Ozon, если он уже
                # ответил 403 или не загрузился.
                if "Ozon homepage" in name:
                    status = result.get("http_status")

                    if status == 403:
                        set_stage("ozon_returned_403")
                        break

                    if result.get("error"):
                        set_stage("ozon_navigation_failed")
                        break

            with lock:
                state["status"] = "COMPLETED"
                state["stage"] = "finished"

    except Exception as exc:
        error_data = {
            "type": type(exc).__name__,
            "message": str(exc)[:3000],
            "repr": repr(exc)[:3000],
            "traceback": traceback.format_exc()[-8000:],
        }

        with lock:
            state["status"] = "ERROR"
            state["error"] = error_data

        print(
            "GLUKOLOV TEST ERROR: "
            + json.dumps(error_data, ensure_ascii=False),
            flush=True,
        )

    finally:
        set_stage("cleanup")

        if context:
            try:
                context.close()
            except Exception as exc:
                with lock:
                    state["cleanup_context_error"] = {
                        "type": type(exc).__name__,
                        "message": str(exc)[:1000],
                    }

        if browser:
            try:
                browser.close()
            except Exception as exc:
                with lock:
                    state["cleanup_browser_error"] = {
                        "type": type(exc).__name__,
                        "message": str(exc)[:1000],
                    }

        with lock:
            state["running"] = False
            state["finished_at"] = now()

            if state["status"] == "RUNNING":
                state["status"] = "COMPLETED"

            state["stage"] = "finished"


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

        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urlsplit(self.path).path

        if path == "/health":
            return self.send_json({
                "ok": True,
                "service": "glukolov-ozon-test",
                "browser": "firefox",
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
                        "status_url": "/status",
                    }, 409)

                # Сразу резервируем запуск, чтобы два
                # одновременных запроса не запустили два теста.
                state.update({
                    "running": True,
                    "status": "STARTING",
                    "started_at": now(),
                    "finished_at": None,
                    "browser": "firefox",
                    "stage": "queued",
                    "proxy": proxy_info(),
                    "results": [],
                    "error": None,
                })

            def worker():
                # run_tests обычно сам устанавливает состояние,
                # но здесь уже установлен running=True.
                run_tests()

            thread = threading.Thread(
                target=worker,
                daemon=True,
            )
            thread.start()

            return self.send_json({
                "accepted": True,
                "message": "Firefox proxy test started",
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
        f"Glukolov diagnostic started on port {PORT}",
        flush=True,
    )

    server.serve_forever()
