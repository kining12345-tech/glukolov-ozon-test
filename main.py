import os
import json
import asyncio
from html import unescape
from http.server import BaseHTTPRequestHandler, HTTPServer

from playwright.async_api import async_playwright

PORT = int(os.environ.get("PORT", "8080"))

PROXY_HOST = os.environ.get("PROXY_HOST", "")
PROXY_PORT = os.environ.get("PROXY_PORT", "")
PROXY_USER = os.environ.get("PROXY_USER", "")
PROXY_PASS = os.environ.get("PROXY_PASS", "")


async def inspect_page(page, url):
    result = {
        "requested_url": url,
    }

    try:
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=45000,
        )

        result["http_status"] = (
            response.status if response else None
        )
        result["final_url"] = page.url

        await page.wait_for_timeout(4000)

        result["title"] = await page.title()

        text = await page.locator("body").inner_text(
            timeout=10000
        )

        result["visible_text"] = " ".join(
            text.split()
        )[:2500]

        result["page_length"] = len(text)

        result["markers"] = {
            "connection_error": (
                "похоже, нет соединения" in text.lower()
            ),
            "captcha": (
                "captcha" in text.lower()
                or "капч" in text.lower()
            ),
            "robot_check": (
                "подтвердите, что вы человек" in text.lower()
                or "verify you are human" in text.lower()
            ),
            "product_signals": any(
                marker in text.lower()
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
        result["details"] = str(e)[:1000]
        result["current_url"] = page.url

    return result


async def run_test():
    if not all([
        PROXY_HOST,
        PROXY_PORT,
        PROXY_USER,
        PROXY_PASS,
    ]):
        return {
            "error": "Proxy environment variables are missing"
        }

    output = {
        "project": "Glukolov Chromium proxy test",
        "proxy_endpoint": f"{PROXY_HOST}:{PROXY_PORT}",
        "browser": "Chromium",
    }

    async with async_playwright() as p:
        browser = None

        try:
            browser = await p.chromium.launch(
                headless=True,
                proxy={
                    "server": (
                        f"http://{PROXY_HOST}:{PROXY_PORT}"
                    ),
                    "username": PROXY_USER,
                    "password": PROXY_PASS,
                },
                args=[
                    "--disable-dev-shm-usage",
                ],
            )

            context = await browser.new_context(
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                viewport={
                    "width": 1365,
                    "height": 900,
                },
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
                    "reason": (
                        "Homepage did not pass the initial check"
                    ),
                }

            await context.close()

        except Exception as e:
            output["browser_error"] = type(e).__name__
            output["browser_error_details"] = str(e)[:1500]

        finally:
            if browser:
                await browser.close()

    return output


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            payload = {
                "status": "ok",
                "project": "Glukolov Chromium proxy test",
            }
        else:
            try:
                payload = asyncio.run(run_test())
            except Exception as e:
                payload = {
                    "error": type(e).__name__,
                    "details": str(e)[:1500],
                }

        data = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header(
            "Content-Length",
            str(len(data)),
        )
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


print(
    f"Glukolov Chromium proxy test listening on {PORT}",
    flush=True,
)

HTTPServer(
    ("0.0.0.0", PORT),
    Handler,
).serve_forever()
