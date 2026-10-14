async def inspect(page):

    print(
        "OPEN OZON",
        flush=True
    )

    result = {}

    try:

        response = await page.goto(
            "https://www.ozon.ru/",
            wait_until="commit",
            timeout=30000
        )


        print(
            "PAGE COMMITTED",
            flush=True
        )


        result["status"] = (
            response.status
            if response
            else None
        )

        result["url_after_commit"] = page.url


        await page.wait_for_timeout(10000)


        print(
            "READ HTML",
            flush=True
        )


        title = await page.title()

        result["title"] = title


        result["url_after_wait"] = page.url


        html = await page.content()


        result["html_length"] = len(html)


        cookies = await page.context.cookies()

        result["cookie_count"] = len(cookies)


        low = html.lower()


        result["markers"] = {

            "antibot":
                "antibot challenge" in low,

            "robot":
                "не робот" in low
                or "verify" in low,

            "challenge":
                "challenge" in low,

            "tilegrid":
                "tilegrid2" in low,

            "pricev2":
                "pricev2" in low,

            "product":
                "/product/" in low,

            "ozon":
                "ozon" in low

        }


        result["html_start"] = html[:2000]


        print(
            "HTML SIZE:",
            len(html),
            flush=True
        )


    except Exception as e:

        print(
            "ERROR:",
            str(e),
            flush=True
        )

        result["error"] = type(e).__name__
        result["details"] = str(e)[:2000]


    return result
