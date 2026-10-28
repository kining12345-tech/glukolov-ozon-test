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
        "configured": bool(PROXY_URL or (PROXY_HOST and PROXY_PORT)),
        "host": PROXY_HOST or ("configured in PROXY_URL" if PROXY_URL else None),
        "port": PROXY_PORT or None,
        "authentication_configured": bool(PROXY_USER and PROXY_PASS),
        "credentials_hidden": True,
        "protocols_to_test": ["http", "socks5"],
    }


def get_proxy(protocol):
    if PROXY_URL:
        # Не переопределяем пользовательский URL автоматически.
        server = PROXY_URL
    else:
        if not PROXY_HOST or not PROXY_PORT:
            raise RuntimeError("PROXY_HOST or PROXY_PORT is missing")

        server = f"{protocol}://{PROXY_HOST}:{PROXY_PORT}"

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


def test_page(page, protocol, target, url):
    result = {
        "protocol": protocol,
        "target": target,
        "started_at": now(),
        "url": url,
    }

    try:
        response = page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=TIMEOUT,
        )

        result["http_status"] = response.status if response else None
        result["final_url"] = page.url

        try:
            result["title"] = page.title()
        except Exception as exc:
            result["title_error"] = str(exc)[:500]

        try:
            data = page.evaluate("""() => {
                const body = document.body?.innerText || '';
                const html = document.documentElement?.outerHTML || '';

                return {
                    body_preview: body.slice(0, 500),
                    body_length: body.length,
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
                        /captcha|похоже, нет соединения|выключите vpn/i.test(body)
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
                result["ip_parse_error"] = True

    except Exception as exc:
        result["error"] = {
            "type": type(exc).__name__,
            "message": str(exc)[:2000],
        }

    result["finished_at"] = now()
    return result


def run_tests():
    try:
        if not PROXY_URL and not (PROXY_HOST and PROXY_PORT):
            raise RuntimeError("Proxy configuration is missing")

        with sync_playwright() as p:
            for protocol in ("http", "socks5"):
                set_stage(f"launching_chromium_{protocol}")

                protocol_result = {
                    "protocol": protocol,
                    "started_at": now(),
                    "launch_ok": False,
                }

                with lock:
                    state["results"].append(protocol_result)

                browser = None
                context = None

                try:
                    proxy = get_proxy(protocol)

                    browser = p.chromium.launch(
                        headless=True,
                        proxy=proxy,
                        timeout=TIMEOUT,
                    )

                    protocol_result["launch_ok"] = True
                    protocol_result["launch_error"] = None

                    context = browser.new_context(
                        viewport={"width": 900, "height": 700},
                        locale="ru-RU",
                        timezone_id="Europe/Moscow",
                    )

                    page = context.new_page()
                    page.set_default_timeout(10000)

                    for target, url in [
                        (
                            "External IP",
                            "https://api.ipify.org?format=json",
                        ),
                        (
                            "Ozon homepage",
                            "https://www.ozon.ru/",
                        ),
                    ]:
                        set_stage(f"{protocol}: {target}")

                        result = test_page(
                            page,
                            protocol,
                            target,
                            url,
                        )

                        with lock:
                            state["results"].append(result)

                        if target == "Ozon homepage":
                            # 403 — это результат HTTP-запроса,
                            # а не ошибка запуска браузера.
                            break

                except Exception as exc:
                    protocol_result["launch_error"] = {
                        "type": type(exc).__name__,
                        "message": str(exc)[:2000],
                    }

                    print(
                        f"Protocol {protocol} error: "
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

                protocol_result["finished_at"] = now()

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

    finally:
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
            target=run_tests,
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
                "proxy": proxy_info(),
                "time": now(),
            })

        if path == "/status":
            with lock:
                snapshot = json.loads(json.dumps(state))
            return self.send_json(snapshot)

        if path == "/test-http":
            if not start_test():
                with lock:
                    snapshot = json.loads(json.dumps(state))

                return self.send_json({
                    "accepted": False,
                    "message": "Test already running",
                    "status": snapshot,
                }, 409)

            return self.send_json({
                "accepted": True,
                "message": "HTTP and SOCKS5 tests started",
                "status_url": "/status",
            }, 202)

        return self.send_json({
            "error": "not_found",
            "available": ["/health", "/test-http", "/status"],
        }, 404)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        Handler,
    )

    print("Glukolov proxy protocol diagnostic started", flush=True)
    server.serve_forever()
