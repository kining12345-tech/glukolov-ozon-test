import os
import json
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer

from playwright.async_api import async_playwright


PORT = int(os.environ.get("PORT", "8080"))

PROXY_HOST = os.environ.get("PROXY_HOST", "")
PROXY_PORT = os.environ.get("PROXY_PORT", "")
PROXY_USER = os.environ.get("PROXY_USER", "")
PROXY_PASS = os.environ.get("PROXY_PASS")


async def inspect_page(page, url):

    result = {
        "requested_url": url
    }

    try:
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        result["http_status"] = (
            response.status if response else None
        )

        result["final_url"] = page.url

        await page.wait_for_timeout(5000)

        result["title"] = await page.title()

        text = await page.locator("body").inner_text(
            timeout=15000
        )

        result["visible_text"] = " ".join(
            text.split()
        )[:3000]

        result["page_length"] = len(text)

        low = text.lower()

        result["markers"] = {
            "connection_error": (
                "похоже, нет соединения" in low
            ),

            "captcha": (
                "captcha" in low
                or "капч" in low
            ),

            "robot_check": (
                "verify you are human" in low
                or "подтвердите, что вы человек" in low
            ),

            "product_signals": any(
                x in low
                for x in [
                    "добавить в корзину",
                    "купить",
                    "отзывы",
                    "рейтинг",
                    "ozon fresh",
                ]
            )
        }


        # Проверка наличия данных страницы
        result["html_length"] = len(
            await page.content()
        )


    except Exception as e:

        result["error"] = type(e).__name__
        result["details"] = str(e)[:1500]
        result["current_url"] = page.url


    return result



async def run_test():

    output = {
        "project": "Glukolov Android Chromium proxy test",
        "proxy": f"{PROXY_HOST}:{PROXY_PORT}",
        "browser": "Chromium mobile profile"
    }


    if not all([
        PROXY_HOST,
        PROXY_PORT,
        PROXY_USER,
        PROXY_PASS
    ]):

        return {
            "error": "Proxy environment variables are missing"
        }



    async with async_playwright() as p:

        browser = None

        try:

            browser = await p.chromium.launch(

                executable_path="/usr/bin/chromium",

                headless=True,

                proxy={
                    "server":
                        f"http://{PROXY_HOST}:{PROXY_PORT}",

                    "username": PROXY_USER,

                    "password": PROXY_PASS,
                },


                args=[

                    "--no-sandbox",

                    "--disable-dev-shm-usage",

                    "--disable-blink-features=AutomationControlled",

                ],
            )



            context = await browser.new_context(

                user_agent=(

                    "Mozilla/5.0 "
                    "(Linux; Android 13; M2101K6G) "

                    "AppleWebKit/537.36 "

                    "(KHTML, like Gecko) "

                    "Chrome/130.0.0.0 "

                    "Mobile Safari/537.36"

                ),


                locale="ru-RU",

                timezone_id="Europe/Moscow",


                viewport={

                    "width": 412,

                    "height": 915,

                },


                device_scale_factor=2,

                is_mobile=True,

                has_touch=True,

            )



            page = await context.new_page()



            output["ozon_home"] = await inspect_page(

                page,

                "https://www.ozon.ru/"

            )



            home = output["ozon_home"]


            if (

                home.get("http_status") == 200

                and not home.get(
                    "markers",
                    {}
                ).get(
                    "connection_error"
                )

            ):


                output["ozon_category"] = await inspect_page(

                    page,

                    "https://www.ozon.ru/category/zhenskaya-odezhda-7501/"

                )


            else:


                output["ozon_category"] = {

                    "skipped": True,

                    "reason":
                        "Homepage check failed"

                }



            await context.close()



        except Exception as e:


            output["browser_error"] = type(e).__name__

            output["browser_error_details"] = str(e)[:2000]



        finally:

            if browser:

                await browser.close()



    return output





class Handler(BaseHTTPRequestHandler):


    def do_GET(self):


        if self.path == "/health":

            payload = {

                "status": "ok",

                "project":
                    "Glukolov Android Chromium proxy test"

            }


        else:


            try:

                payload = asyncio.run(
                    run_test()
                )


            except Exception as e:

                payload = {

                    "error": type(e).__name__,

                    "details": str(e)[:2000]

                }



        data = json.dumps(

            payload,

            ensure_ascii=False,

            indent=2

        ).encode("utf-8")



        self.send_response(200)

        self.send_header(

            "Content-Type",

            "application/json; charset=utf-8"

        )

        self.send_header(

            "Content-Length",

            str(len(data))

        )


        self.end_headers()


        try:

            self.wfile.write(data)

        except BrokenPipeError:

            pass



    def log_message(self, fmt, *args):

        print(fmt % args, flush=True)




print(

    f"Glukolov Android Chromium proxy test listening on {PORT}",

    flush=True

)



HTTPServer(

    ("0.0.0.0", PORT),

    Handler

).serve_forever()
