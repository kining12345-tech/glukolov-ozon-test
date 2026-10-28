import os
import json
import threading
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from camoufox.sync_api import Camoufox


PORT = int(os.getenv("PORT", "8080"))
TIMEOUT = 45000

PROXY_HOST = os.getenv("PROXY_HOST", "").strip()
PROXY_PORT = os.getenv("PROXY_PORT", "").strip()
PROXY_USER = os.getenv("PROXY_USER", "")
PROXY_PASS = os.getenv("PROXY_PASS", "")
PROXY_URL = os.getenv("PROXY_URL", "").strip()

lock = threading.RLock()

state = {
    "running": False,
    "status": "IDLE",
    "started_at": None,
    "finished_at": None,
    "stage": None,
    "proxy": {},
    "results": [],
    "error": None,
}


def now():
    return datetime.now(timezone.utc).isoformat()


def proxy_info():
    return {
        "configured": bool(
            PROXY_URL or (PROXY_HOST and PROXY_PORT)
        ),
        "host": PROXY_HOST or (
            "configured in PROXY_URL" if PROXY_URL else None
        ),
        "port": PROXY_PORT or None,
        "authentication_configured": bool(
            PROXY_USER and PROXY_PASS
        ),
        "credentials_hidden": True,
        "browser": "Camoufox",
        "protocol": "HTTP",
    }


def get_proxy():
    if PROXY_URL:
        server = PROXY_URL
    else:
        if not PROXY_HOST or not PROXY_PORT:
            raise RuntimeError(
                "PROXY_HOST or PROXY_PORT is missing"
            )

        server = f"http://{PROXY_HOST}:{PROXY_PORT}"

    proxy = {"server": server}

    if PROXY_USER:
        proxy["username"] = PROXY_USER

    if PROXY_PASS:
        proxy["password"] = PROXY_PASS

    return proxy


def set_stage(value):
    with lock:
        state["stage"] = value
        state["last_activity_at"] = now()


def inspect_page(page):
    return page.evaluate("""() => {
        const body = document.body?.innerText || '';
        const html = document.documentElement?.outerHTML || '';

        const links = Array.from(
            document.querySelectorAll('a[href*="/product/"]')
        );

        const prices = Array.from(
            document.querySelectorAll(
                '[class*="price"], [data-widget*="price"]'
            )
        ).slice(0, 30).map(el => ({
            text: (el.innerText || '').trim().slice(0, 150),
            className: String(el.className || '').slice(0, 150)
        }));

        return {
            title: document.title,
            body_preview: body.slice(0, 1500),
            body_length: body.length,
            html_length: html.length,
            product_links: links.length,
            product_link_samples: links.slice(0, 10).map(a => ({
                text: (a.innerText || '').trim().slice(0, 150),
                href: a.href
            })),
            price_markers:
                html.includes('priceV2') ||
                html.includes('price_v2'),
            widget_states: html.includes('widgetStates'),
            tile_grid: html.includes('tileGrid'),
            price_elements: prices,
            challenge_text:
                /captcha|похоже, нет соединения|выключите vpn|доступ ограничен/i
                    .test(body)
        };
    }""")


def run_camoufox_test():
    browser = None
    context = None

    try:
        if not PROXY_URL and not (
            PROXY_HOST and PROXY_PORT
        ):
            raise RuntimeError(
                "Proxy configuration is missing"
            )

        set_stage("launching_camoufox")

        result = {
            "target": "Ozon homepage through Camoufox",
            "browser": "Camoufox",
            "started_at": now(),
            "url": "https://www.ozon.ru/",
        }

        with lock:
            state["results"].append(result)

        proxy = get_proxy()

        # Запускаем только Camoufox.
        # Никаких тестов SOCKS5 и внешнего IP.
        with Camoufox(
            headless=True,
            proxy=proxy,
            geoip=True,
        ) as browser:
            set_stage("opening_ozon")

            context = browser.new_context(
                viewport={
                    "width": 1365,
                    "height": 900,
                },
                locale="ru-RU",
                timezone_id="Europe/Moscow",
            )

            page = context.new_page()
            page.set_default_timeout(15000)

            response = None

            try:
                response = page.goto(
                    "https://www.ozon.ru/",
                    wait_until="domcontentloaded",
                    timeout=TIMEOUT,
                )

                result["http_status"] = (
                    response.status if response else None
                )
                result["final_url"] = page.url

                # Даём странице немного времени на выполнение JS.
                set_stage("waiting_for_catalog")
                page.wait_for_timeout(4000)

                result.update(inspect_page(page))

            except Exception as exc:
                result["navigation_error"] = {
                    "type": type(exc).__name__,
                    "message": str(exc)[:2000],
                    "final_url": page.url,
                }

                try:
                    result.update(inspect_page(page))
                except Exception:
                    pass

            result["finished_at"] = now()

            status = result.get("http_status")
            products = result.get("product_links", 0)
            challenge = result.get("challenge_text", False)
            price_markers = result.get("price_markers", False)

            if status == 200 and products > 0:
                result["verdict"] = "PRODUCTS_LOADED"
            elif status == 200 and (
                price_markers or result.get("widget_states")
            ):
                result["verdict"] = "PAGE_LOADED_CHECK_PRODUCT_DATA"
            elif status == 403 or challenge:
                result["verdict"] = "OZON_BLOCKED_OR_CHALLENGE"
            elif result.get("navigation_error"):
                result["verdict"] = "NAVIGATION_FAILED"
            else:
                result["verdict"] = "NO_PRODUCTS_DETECTED"

            with lock:
                state["status"] = "COMPLETED"
                state["stage"] = "finished"

    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "message": str(exc)[:3000],
            "traceback": traceback.format_exc()[-6000:],
        }

        with lock:
            state["status"] = "ERROR"
            state["error"] = error

        print(
            "Camoufox test error: "
            f"{type(exc).__name__}: {str(exc)[:1000]}",
            flush=True,
        )

    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass

        if browser:
            try:
                browser.close()
            except Exception:
                pass

        with lock:
            state["running"] = False
            state["finished_at"] = now()
            state["last_activity_at"] = now()

            if state["status"] == "RUNNING":
                state["status"] = "COMPLETED"

            state["stage"] = "finished"


def start_test():
    with lock:
        if state["running"]:
            return False

        state.update({
            "running": True,
            "status": "RUNNING",
            "started_at": now(),
            "finished_at": None,
            "stage": "starting",
            "proxy": proxy_info(),
            "results": [],
            "error": None,
        })

        thread = threading.Thread(
            target=run_camoufox_test,
            daemon=True,
        )

        try:
            thread.start()
        except Exception as exc:
            state.update({
                "running": False,
                "status": "ERROR",
                "stage": "thread_start_failed",
                "finished_at": now(),
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc)[:2000],
                },
            })
            return False

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
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
        self.end_headers()

        try:
            self.wfile.write(body)
        except (
            BrokenPipeError,
            ConnectionResetError,
        ):
            pass

    def do_GET(self):
        path = urlsplit(self.path).path

        if path == "/health":
            return self.send_json({
                "ok": True,
                "service": "glukolov-ozon-test",
                "proxy": proxy_info(),
                "time": now(),
            })

        if path == "/status":
            with lock:
                snapshot = json.loads(
                    json.dumps(state)
                )
            return self.send_json(snapshot)

        # Сохраняем старый маршрут для совместимости.
        # Теперь он запускает только Camoufox.
        if path in ("/test-http", "/test-camoufox"):
            if not start_test():
                with lock:
                    snapshot = json.loads(
                        json.dumps(state)
                    )

                return self.send_json({
                    "accepted": False,
                    "message": "Test already running",
                    "status": snapshot,
                }, 409)

            return self.send_json({
                "accepted": True,
                "message": "Camoufox Ozon test started",
                "status_url": "/status",
            }, 202)

        return self.send_json({
            "error": "not_found",
            "available": [
                "/health",
                "/status",
                "/test-camoufox",
                "/test-http",
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
        "Glukolov Camoufox diagnostic started",
        flush=True,
    )

    server.serve_forever()
