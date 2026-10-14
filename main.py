import os
import sys
import json
import glob
import time
import asyncio
import threading
import traceback
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PORT = int(os.environ.get("PORT", "8000"))

STATE = {
    "running": False,
    "result": None,
    "last_error": None,
}

LOCK = threading.Lock()


def browser_diagnostics():
    import playwright

    home = os.path.expanduser("~")
    cache = os.environ.get(
        "PLAYWRIGHT_BROWSERS_PATH",
        os.path.join(home, ".cache", "ms-playwright")
    )

    candidates = []
    for pattern in [
        os.path.join(cache, "firefox-*"),
        os.path.join(cache, "firefox-*", "firefox", "firefox"),
        "/usr/bin/firefox",
        "/usr/bin/firefox-esr",
    ]:
        candidates.extend(glob.glob(pattern))

    return {
        "python": sys.version,
        "executable": sys.executable,
        "working_directory": os.getcwd(),
        "home": home,
        "playwright_version": getattr(playwright, "__version__", "unknown"),
        "PLAYWRIGHT_BROWSERS_PATH": os.environ.get(
            "PLAYWRIGHT_BROWSERS_PATH"
        ),
        "browser_cache": cache,
        "browser_cache_exists": os.path.exists(cache),
        "browser_cache_contents": (
            os.listdir(cache) if os.path.isdir(cache) else []
        ),
        "firefox_candidates": candidates,
        "firefox_executable_exists": os.path.exists(
            os.path.join(cache, "firefox-1490", "firefox", "firefox")
        ),
    }


async def run_test():
    from playwright.async_api import async_playwright

    result = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "diagnostics": browser_diagnostics(),
        "steps": [],
    }

    def log(message):
        print(message, flush=True)
        result["steps"].append(message)

    log("STEP 1: diagnostics collected")

    async with async_playwright() as p:
        log("STEP 2: Playwright initialized")

        browser = await asyncio.wait_for(
            p.firefox.launch(
                headless=True,
                proxy={
                    "server": (
                        f"http://{os.environ['PROXY_HOST']}:"
                        f"{os.environ['PROXY_PORT']}"
                    ),
                    "username": os.environ.get("PROXY_USER", ""),
                    "password": os.environ.get("PROXY_PASS", ""),
                },
            ),
            timeout=30,
        )

        log("STEP 3: Firefox launched")

        try:
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Android 13; Mobile; rv:157.0) "
                    "Gecko/157.0 Firefox/157.0"
                ),
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                viewport={"width": 412, "height": 915},
                is_mobile=True,
                has_touch=True,
            )

            page = await context.new_page()
            log("STEP 4: opening Ozon")

            response = await page.goto(
                "https://www.ozon.ru/",
                wait_until="domcontentloaded",
                timeout=60000,
            )

            log("STEP 5: Ozon response received")

            await page.wait_for_timeout(5000)

            html = await page.content()

            result["ozon"] = {
                "url": page.url,
                "title": await page.title(),
                "status": response.status if response else None,
                "html_length": len(html),
                "markers": {
                    "antibot": "antibot" in html.lower(),
                    "challenge": "challenge" in html.lower(),
                    "connection_error": "нет соединения" in html.lower(),
                    "products": (
                        "product" in html.lower()
                        or "товар" in html.lower()
                    ),
                },
            }

            log("STEP 6: page analysis completed")
            result["success"] = True

            await context.close()
        finally:
            await browser.close()

    return result


def worker():
    try:
        print("FIREFOX TEST START", flush=True)

        result = asyncio.run(run_test())

        with LOCK:
            STATE["result"] = result
            STATE["last_error"] = None

        print("FIREFOX TEST COMPLETE", flush=True)

    except BaseException as exc:
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }

        with LOCK:
            STATE["last_error"] = error

        print("FIREFOX TEST FAILED", flush=True)
        print(error["traceback"], flush=True)

    finally:
        with LOCK:
            STATE["running"] = False


class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        body = json.dumps(
            data, ensure_ascii=False, indent=2
        ).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/":
            return self.send_json({
                "app": "Glukolov Firefox diagnostic",
                "routes": ["/health", "/diagnostics", "/test", "/status"],
            })

        if path == "/health":
            return self.send_json({"status": "ok", "port": PORT})

        if path == "/diagnostics":
            try:
                return self.send_json(browser_diagnostics())
            except Exception as exc:
                return self.send_json({
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }, 500)

        if path == "/test":
            with LOCK:
                if STATE["running"]:
                    return self.send_json({
                        "status": "already_running",
                        "message": "Тест уже выполняется",
                    })

                STATE["running"] = True
                STATE["result"] = None
                STATE["last_error"] = None

            threading.Thread(target=worker, daemon=True).start()

            return self.send_json({
                "status": "started",
                "message": "Диагностический тест запущен",
                "status_url": "/status",
            })

        if path == "/status":
            with LOCK:
                return self.send_json(dict(STATE))

        return self.send_json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


print(f"Glukolov Firefox diagnostic listening on {PORT}", flush=True)

server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
server.serve_forever()
