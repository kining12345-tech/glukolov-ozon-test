import urllib.request
import json

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

opener = urllib.request.build_opener(NoRedirect)

print("=== IP ===")

try:
    r = urllib.request.urlopen(
        "https://ipapi.co/json/",
        timeout=10
    )
    print(r.read().decode())
except Exception as e:
    print("IP ERROR", e)


print("\n=== OZON ===")

req = urllib.request.Request(
    "https://www.ozon.ru/",
    headers={
        "User-Agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/130 Safari/537.36",
        "Accept-Language":
            "ru-RU,ru;q=0.9"
    }
)

try:
    r = opener.open(req, timeout=20)
    print("HTTP:", r.status)
    print("URL:", r.url)
    print(r.read(1000).decode("utf-8", "replace"))

except urllib.error.HTTPError as e:
    print("HTTP:", e.code)
    print("Location:", e.headers.get("Location"))
    print("Server:", e.headers)

except Exception as e:
    print("ERROR:", repr(e))
