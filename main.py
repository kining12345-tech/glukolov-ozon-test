import os
import json
import asyncio
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from playwright.async_api import async_playwright


PORT = int(os.environ.get("PORT", "8000"))

PROXY_HOST = os.environ.get("PROXY_HOST", "")
PROXY_PORT = os.environ.get("PROXY_PORT", "")
PROXY_USER = os.environ.get("PROXY_USER", "")
PROXY_PASS = os.environ.get("PROXY_PASS", "")

LOCK = threading.Lock()

STATE = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "result": None,
}


def now():
    return datetime.now(timezone.utc).isoformat()


def proxy_config():
    if not PROXY_HOST or not PROXY_PORT:
        raise RuntimeError("PROXY_HOST or PROXY_PORT is missing")

    config = {
        "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
    }

    if PROXY_USER:
        config["username"] = PROXY_USER

    if PROXY_PASS:
        config["password"] = PROXY_PASS

    return config


def update_state(**kwargs):
    with LOCK:
        STATE.update(kwargs)


def get_state():
    with LOCK:
        return dict(STATE)


async def test_http_request(playwright):
    print("HTTP TEST START", flush=True)

    result = {
        "method": "Playwright APIRequestContext",
        "requested_url": "https://www.ozon.ru/",
    }

    request = None

    try:
        request = await playwright.request.new_context(
            proxy=proxy_config(),
            timeout=20000,
            ignore_https_errors=False,
            extra_http_headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            },
        )

        response = await request.get(
            "https://www.ozon.ru/",
            max_redirects=5,
        )

        body = await response.body()
        headers = await response.all_headers()
        text = body.decode("utf-8", errors="replace")
        low = text.lower()

        result.update({
            "http_status": response.status,
            "final_url": response.url,
            "body_length": len(body),
            "content_type": headers.get("content-type"),
            "server": headers.get("server"),
            "location": headers.get("location"),
            "markers": {
                "antibot": "antibot" in low,
                "challenge": "challenge" in low,
                "connection_error": "похоже, нет соединения" in low,
                "captcha": "captcha" in low or "капч" in low,
                "tilegrid": "tilegrid2" in low,
                "pricev2": "pricev2" in low,
            },
            "body_start": text[:1200],
        })

        print(
            f"HTTP TEST DONE: {response.status}, bytes={len(body)}",
            flush=True,
        )

    except Exception as e:
        result["error"] = type(e).__name__
        result["details"] = str(e)[:1500]

        print("HTTP TEST ERROR:", repr(e), flush=True)

    finally:
        if request:
            await request.dispose()

    return result


async def test_chromium(playwright):
    print("CHROMIUM TEST START", flush=True)

    result = {
        "requested_url": "https://www.ozon.ru/",
    }

    browser = None
    context = None

    try:
        browser = await playwright.chromium.launch(
            executable_path="/usr/bin/chromium",
            headless=True,
            proxy=proxy_config(),
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context = await browser.new_context(
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={
                "width": 1280,
                "height": 900,
            },
        )

        page = await context.new_page()

        response = await page.goto(
            "https://www.ozon.ru/",
            wait_until="commit",
            timeout=30000,
        )

        result["http_status"] = (
            response.status if response else None
        )

        result["url_after_commit"] = page.url

        await page.wait_for_timeout(5000)

        result["final_url"] = page.url
        result["title"] = await page.title()

        html = await page.content()
        low = html.lower()

        result["html_length"] = len(html)

        result["markers"] = {
            "antibot": "antibot" in low,
            "challenge": "challenge" in low,
            "connection_error": "похоже, нет соединения" in low,
            "captcha": "captcha" in low or "капч" in low,
            "tilegrid": "tilegrid2" in low,
            "pricev2": "pricev2" in low,
            "product": "/product/" in low,
        }

        result["html_start"] = html[:1200]

        print(
            f"CHROMIUM TEST DONE: status={result['http_status']}",
            flush=True,
        )

    except Exception as e:
        result["error"] = type(e).__name__
        result["details"] = str(e)[:1500]

        print("CHROMIUM TEST ERROR:", repr(e), flush=True)

    finally:
        if context:
            try:
                await context.close()
            except Exception:
                pass

        if browser:
            try:
                await browser.close()
            except Exception:
                pass

    return result


async def run_tests():
    output = {
        "project": "Glukolov Ozon network comparison",
        "started_at": now(),
    }

    async with async_playwright() as playwright:
        output["http_request"] = await test_http_request(playwright)
        output["chromium"] = await test_chromium(playwright)

    output["finished_at"] = now()

    return output


def worker():
    update_state(
        status="running",
        started_at=now(),
        finished_at=None,
        result=None,
    )

    print("COMPARISON TEST START", flush=True)

    try:
        result = asyncio.run(run_tests())

        update_state(
            status="completed",
            finished_at=now(),
            result=result,
        )

        print("COMPARISON TEST COMPLETED", flush=True)

    except Exception as e:
        update_state(
            status="error",
            finished_at=now(),
            result={
                "error": type(e).__name__,
                "details": str(e)[:2000],
            },
        )

        print("WORKER ERROR:", repr(e), flush=True)


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
        path = urlparse(self.path).path

        if path == "/health":
            return self.send_json({
                "status": "ok",
                "project": "Glukolov Ozon network comparison",
            })

        if path == "/test":
            with LOCK:
                if STATE["status"] == "running":
                    return self.send_json({
                        "status": "already_running",
                        "check": "/status",
                    })

                STATE.update({
                    "status": "running",
                    "started_at": now(),
                    "finished_at": None,
                    "result": None,
                })

            threading.Thread(
                target=worker,
                daemon=True,
            ).start()

            return self.send_json({
                "status": "started",
                "check": "/status",
            })

        if path == "/status":
            return self.send_json(get_state())

        return self.send_json({
            "project": "Glukolov Ozon network comparison",
            "routes": ["/health", "/test", "/status"],
        })

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


print("=== GLUKOLOV NETWORK TEST START ===", flush=True)
print(f"=== LISTEN PORT {PORT} ===", flush=True)

ThreadingHTTPServer(
    ("0.0.0.0", PORT),
    Handler,
).serve_forever()
