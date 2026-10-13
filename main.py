import os
import json
import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from playwright.async_api import async_playwright


PORT = int(os.environ.get("PORT", "8080"))

PROXY_HOST = os.environ.get("PROXY_HOST", "")
PROXY_PORT = os.environ.get("PROXY_PORT", "")
PROXY_USER = os.environ.get("PROXY_USER", "")
PROXY_PASS = os.environ.get("PROXY_PASS", "")

TEST_LOCK = threading.Lock()
TEST_STATE = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "result": None,
}


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def inspect_page(page, url):
    result = {"requested_url": url}

    try:
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=45000,
        )

        result["http_status"] = response.status if response else None
        result["final_url"] = page.url

        await page.wait_for_timeout(3000)

        result["title"] = await page.title()

        text = await page.locator("body").inner_text(timeout=10000)
        low = text.lower()

        result["visible_text"] = " ".join(text.split())[:2500]
        result["page_length"] = len(text)
        result["html_length"] = len(await page.content())

        result["markers"] = {
            "connection_error": "похоже, нет соединения" in low,
            "captcha": "captcha" in low or "капч" in low,
            "robot_check": (
                "подтвердите, что вы человек" in low
                or "verify you are human" in low
            ),
            "product_signals": any(
                marker in low
                for marker in (
                    "добавить в корзину",
                    "купить",
                    "отзывы",
                    "рейтинг",
                )
            ),
        }

    except Exception as e:
        result["error"] = type(e).__name__
        result["details"] = str(e)[:1500]
        result["current_url"] = page.url

    return result


async def run_test():
    output = {
        "project": "Glukolov Android Chromium proxy test",
        "browser": "Chromium mobile profile",
        "started_at": utc_now(),
    }

    if not all([PROXY_HOST, PROXY_PORT, PROXY_USER, PROXY_PASS]):
        output["error"] = "Proxy environment variables are missing"
        return output

    output["proxy_endpoint"] = f"{PROXY_HOST}:{PROXY_PORT}"

    async with async_playwright() as p:
        browser = None
        context = None

        try:
            browser = await p.chromium.launch(
                executable_path="/usr/bin/chromium",
                headless=True,
                proxy={
                    "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
                    "username": PROXY_USER,
                    "password": PROXY_PASS,
                },
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )

            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Linux; Android 13; M2101K6G) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0.0.0 Mobile Safari/537.36"
                ),
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                viewport={"width": 412, "height": 915},
                device_scale_factor=2,
                is_mobile=True,
                has_touch=True,
            )

            page = await context.new_page()

            output["ozon_home"] = await inspect_page(
                page,
                "https://www.ozon.ru/",
            )

            home = output["ozon_home"]
            markers = home.get("markers", {})

            if (
                home.get("http_status") == 200
                and not markers.get("connection_error")
            ):
                output["ozon_category"] = await inspect_page(
                    page,
                    "https://www.ozon.ru/category/zhenskaya-odezhda-7501/",
                )
            else:
                output["ozon_category"] = {
                    "skipped": True,
                    "reason": "Homepage check failed",
                }

        except Exception as e:
            output["browser_error"] = type(e).__name__
            output["browser_error_details"] = str(e)[:2000]

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

    output["finished_at"] = utc_now()
    return output


def test_worker():
    global TEST_STATE

    try:
        result = asyncio.run(run_test())

        with TEST_LOCK:
            TEST_STATE["status"] = "completed"
            TEST_STATE["finished_at"] = utc_now()
            TEST_STATE["result"] = result

    except Exception as e:
        with TEST_LOCK:
            TEST_STATE["status"] = "failed"
            TEST_STATE["finished_at"] = utc_now()
            TEST_STATE["result"] = {
                "error": type(e).__name__,
                "details": str(e)[:2000],
            }


class Handler(BaseHTTPRequestHandler):

    def send_json(self, payload, status=200):
        data = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass
        except ConnectionResetError:
            pass

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            return self.send_json({
                "status": "ok",
                "project": "Glukolov Android Chromium proxy test",
            })

        if path == "/":
            return self.send_json({
                "project": "Glukolov Android Chromium proxy test",
                "status": "running",
                "routes": {
                    "health": "/health",
                    "start_test": "/test",
                    "test_status": "/status",
                },
                "message": "Open /test to start the browser test.",
            })

        if path == "/test":
            with TEST_LOCK:
                if TEST_STATE["status"] == "running":
                    return self.send_json({
                        "status": "running",
                        "message": "Test is already running.",
                        "check_result_at": "/status",
                    }, 202)

                TEST_STATE["status"] = "running"
                TEST_STATE["started_at"] = utc_now()
                TEST_STATE["finished_at"] = None
                TEST_STATE["result"] = None

            worker = threading.Thread(
                target=test_worker,
                daemon=True,
            )
            worker.start()

            return self.send_json({
                "status": "started",
                "message": "Chromium/Ozon test started.",
                "check_result_at": "/status",
            }, 202)

        if path == "/status":
            with TEST_LOCK:
                state = dict(TEST_STATE)

            return self.send_json(state)

        return self.send_json({
            "error": "Not found",
            "available_routes": ["/", "/health", "/test", "/status"],
        }, 404)

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


print(
    f"Glukolov Android Chromium proxy test listening on {PORT}",
    flush=True,
)

HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
