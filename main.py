import os
import json
import time
import asyncio
import threading
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from playwright.async_api import async_playwright


PORT = int(os.environ.get("PORT", "8000"))
OZON_URL = "https://www.ozon.ru/"

TEST_STATE = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "result": None,
    "error": None,
}

STATE_LOCK = threading.Lock()
TEST_LOCK = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def proxy_config():
    host = os.environ.get("PROXY_HOST", "").strip()
    port = os.environ.get("PROXY_PORT", "").strip()
    username = os.environ.get("PROXY_USER", "").strip()
    password = os.environ.get("PROXY_PASS", "")

    if not host or not port:
        raise RuntimeError(
            "Proxy is not configured: PROXY_HOST or PROXY_PORT is missing"
        )

    config = {
        "server": f"http://{host}:{port}",
    }

    if username:
        config["username"] = username

    if password:
        config["password"] = password

    return config


def detect_markers(html, title=""):
    text = (html or "").lower()
    title_lower = (title or "").lower()

    return {
        "antibot": any(
            marker in text
            for marker in (
                "antibot challenge",
                "anti-bot challenge",
                "are not a robot",
                "enable javascript to continue",
            )
        ),
        "challenge": any(
            marker in text
            for marker in (
                "__rr=1",
                "challenge",
                "captcha",
                "проверка безопасности",
                "подтвердите, что вы не робот",
            )
        ),
        "connection_error": any(
            marker in text or marker in title_lower
            for marker in (
                "похоже, нет соединения",
                "нет соединения",
                "connection error",
                "connection failed",
            )
        ),
        "captcha": any(
            marker in text
            for marker in (
                "captcha",
                "капча",
                "подтвердите, что вы не робот",
            )
        ),
        "tilegrid": "tilegrid" in text,
        "pricev2": "pricev2" in text,
        "product": (
            "product-card" in text
            or "data-widget=\"searchResultsV2\"" in html
            or "sku" in text
        ),
    }


def safe_body_start(body, limit=1200):
    if not body:
        return ""

    return body[:limit]


async def test_http_request(playwright):
    """
    Прямой HTTP-запрос через прокси.
    Не использует Chromium и не исполняет JavaScript страницы.
    """
    result = {
        "method": "Playwright APIRequestContext",
        "requested_url": OZON_URL,
    }

    context = None

    try:
        context = await playwright.request.new_context(
            proxy=proxy_config(),
            timeout=30000,
            ignore_https_errors=False,
            extra_http_headers={
                "User-Agent": (
                    "Mozilla/5.0 (Linux; Android 13; Mobile) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Mobile Safari/537.36"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,image/avif,"
                    "image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )

        response = await context.get(
            OZON_URL,
            timeout=30000,
        )

        # Важно: APIResponse.headers — свойство, не метод all_headers().
        headers = response.headers

        try:
            body = await response.text()
        except Exception as exc:
            body = ""
            result["body_read_error"] = str(exc)

        result.update({
            "http_status": response.status,
            "ok": response.ok,
            "url": response.url,
            "content_type": headers.get("content-type"),
            "server": headers.get("server"),
            "location": headers.get("location"),
            "body_length": len(body),
            "markers": detect_markers(body),
            "body_start": safe_body_start(body),
        })

    except Exception as exc:
        result.update({
            "error": type(exc).__name__,
            "details": str(exc),
            "traceback": traceback.format_exc()[-3000:],
        })

    finally:
        if context is not None:
            try:
                await context.dispose()
            except Exception:
                pass

    return result


async def test_chromium(playwright):
    """
    Открывает Ozon в Chromium через тот же прокси.
    """
    result = {
        "requested_url": OZON_URL,
    }

    browser = None
    context = None

    try:
        browser = await playwright.chromium.launch(
            headless=True,
            executable_path="/usr/bin/chromium",
            proxy=proxy_config(),
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Linux; Android 13; Mobile) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Mobile Safari/537.36"
            ),
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={
                "width": 412,
                "height": 915,
            },
            is_mobile=True,
            has_touch=True,
            ignore_https_errors=False,
        )

        page = await context.new_page()

        response_status = None
        navigation_error = None

        try:
            response = await page.goto(
                OZON_URL,
                wait_until="commit",
                timeout=30000,
            )

            if response is not None:
                response_status = response.status

        except Exception as exc:
            navigation_error = {
                "type": type(exc).__name__,
                "details": str(exc),
            }

        # Даём странице немного времени на отрисовку.
        await page.wait_for_timeout(5000)

        try:
            final_url = page.url
        except Exception:
            final_url = None

        try:
            title = await page.title()
        except Exception:
            title = ""

        try:
            html = await page.content()
        except Exception as exc:
            html = ""
            result["html_read_error"] = str(exc)

        result.update({
            "http_status": response_status,
            "url_after_commit": final_url,
            "final_url": final_url,
            "title": title,
            "html_length": len(html),
            "markers": detect_markers(html, title),
            "html_start": safe_body_start(html),
        })

        if navigation_error:
            result["navigation_error"] = navigation_error

    except Exception as exc:
        result.update({
            "error": type(exc).__name__,
            "details": str(exc),
            "traceback": traceback.format_exc()[-3000:],
        })

    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass

        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass

    return result


async def run_tests():
    print("=== GLUKOLOV NETWORK TEST START ===", flush=True)

    async with async_playwright() as playwright:
        # Сначала обычный HTTP, затем Chromium.
        http_result = await test_http_request(playwright)
        print(
            "HTTP TEST: "
            + json.dumps(http_result, ensure_ascii=False),
            flush=True,
        )

        chromium_result = await test_chromium(playwright)
        print(
            "CHROMIUM TEST: "
            + json.dumps(chromium_result, ensure_ascii=False),
            flush=True,
        )

    return {
        "project": "Glukolov Ozon network comparison",
        "started_at": TEST_STATE["started_at"],
        "http_request": http_result,
        "chromium": chromium_result,
        "finished_at": now_iso(),
    }


def test_worker():
    try:
        result = asyncio.run(run_tests())

        with STATE_LOCK:
            TEST_STATE["status"] = "completed"
            TEST_STATE["result"] = result
            TEST_STATE["finished_at"] = now_iso()
            TEST_STATE["error"] = None

    except Exception as exc:
        error_data = {
            "type": type(exc).__name__,
            "details": str(exc),
            "traceback": traceback.format_exc()[-5000:],
        }

        print(
            "FATAL TEST ERROR: "
            + json.dumps(error_data, ensure_ascii=False),
            flush=True,
        )

        with STATE_LOCK:
            TEST_STATE["status"] = "failed"
            TEST_STATE["error"] = error_data
            TEST_STATE["finished_at"] = now_iso()

    finally:
        TEST_LOCK.release()


class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        payload = json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"

        if path == "/":
            self.send_json({
                "project": "Glukolov Chromium proxy test",
                "status": "ok",
                "endpoints": {
                    "health": "/health",
                    "start_test": "/test",
                    "results": "/status",
                },
            })
            return

        if path == "/health":
            self.send_json({
                "status": "ok",
                "project": "glukolov-ozon-test",
                "time": now_iso(),
            })
            return

        if path == "/test":
            if not TEST_LOCK.acquire(blocking=False):
                with STATE_LOCK:
                    state = {
                        "status": TEST_STATE["status"],
                        "started_at": TEST_STATE["started_at"],
                    }

                self.send_json({
                    "status": "already_running",
                    "message": "The test is already running.",
                    "state": state,
                    "results_url": "/status",
                }, 409)
                return

            with STATE_LOCK:
                TEST_STATE["status"] = "running"
                TEST_STATE["started_at"] = now_iso()
                TEST_STATE["finished_at"] = None
                TEST_STATE["result"] = None
                TEST_STATE["error"] = None

            worker = threading.Thread(
                target=test_worker,
                daemon=True,
            )
            worker.start()

            self.send_json({
                "status": "started",
                "started_at": TEST_STATE["started_at"],
                "message": "HTTP and Chromium tests have started.",
                "results_url": "/status",
            }, 202)
            return

        if path == "/status":
            with STATE_LOCK:
                snapshot = {
                    "status": TEST_STATE["status"],
                    "started_at": TEST_STATE["started_at"],
                    "finished_at": TEST_STATE["finished_at"],
                    "result": TEST_STATE["result"],
                    "error": TEST_STATE["error"],
                }

            self.send_json(snapshot)
            return

        self.send_json({
            "error": "Not found",
            "available_endpoints": [
                "/health",
                "/test",
                "/status",
            ],
        }, 404)

    def log_message(self, fmt, *args):
        print(
            f"[HTTP] {self.client_address[0]} - {fmt % args}",
            flush=True,
        )


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)

    print("=== GLUKOLOV NETWORK TEST SERVER ===", flush=True)
    print(f"=== LISTEN PORT {PORT} ===", flush=True)

    server.serve_forever()


if __name__ == "__main__":
    main()
