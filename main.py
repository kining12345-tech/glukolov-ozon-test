import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, quote, urljoin

import requests

try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    curl_requests = None
    CURL_CFFI_AVAILABLE = False


PORT = int(os.environ.get("PORT", "8000"))

state_lock = threading.Lock()
state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "status": "IDLE",
    "results": [],
    "error": None,
}


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_location(value):
    if not value:
        return None

    try:
        parsed = urlparse(value)
        result = parsed.path or "/"

        if parsed.query:
            result += "?" + parsed.query

        if parsed.fragment:
            result += "#" + parsed.fragment

        return result[:2000]
    except Exception:
        return None


def get_proxy_url():
    direct_url = os.getenv("PROXY_URL")
    if direct_url:
        return direct_url

    host = os.getenv("PROXY_HOST")
    port = os.getenv("PROXY_PORT")

    if not host or not port:
        return None

    user = os.getenv("PROXY_USER")
    password = os.getenv("PROXY_PASS")

    if user:
        auth = quote(user, safe="")
        if password:
            auth += ":" + quote(password, safe="")
        return f"http://{auth}@{host}:{port}"

    return f"http://{host}:{port}"


def make_headers():
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/141.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/json,"
            "application/signed-exchange;v=b3;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
    }


def inspect_response(response):
    body = response.text[:50000]
    lower = body.lower()

    return {
        "status": response.status_code,
        "url_path": safe_location(response.url),
        "location": safe_location(response.headers.get("Location")),
        "content_type": response.headers.get("Content-Type"),
        "server": response.headers.get("Server"),
        "body_length": len(response.content),
        "body_preview": body[:800],
        "markers": {
            "antibot": "antibot" in lower,
            "challenge": "challenge" in lower,
            "captcha": "captcha" in lower,
            "incident_id": "incidentid" in lower,
            "rr_redirect": "__rr=1" in lower
            or "__rr=1" in response.headers.get("Location", ""),
            "widget_states": "widgetStates" in body,
            "tile_grid": "tileGrid2" in body,
            "price_v2": "priceV2" in body,
            "challenge_url": "challengeurl" in lower,
        },
    }


def request_once(url, proxy_url, library="requests"):
    kwargs = {
        "headers": make_headers(),
        "timeout": 15,
        "allow_redirects": False,
    }

    if proxy_url:
        kwargs["proxies"] = {
            "http": proxy_url,
            "https": proxy_url,
        }

    if library == "curl_cffi":
        if not CURL_CFFI_AVAILABLE:
            raise RuntimeError("curl_cffi is not installed")

        return curl_requests.get(
            url,
            impersonate="chrome",
            **kwargs,
        )

    return requests.get(url, **kwargs)


def trace_redirects(label, initial_url, proxy_url, library):
    result = {
        "test": label,
        "library": library,
        "proxy": "configured" if proxy_url else "direct",
        "initial_url_path": safe_location(initial_url),
        "hops": [],
        "error": None,
    }

    current_url = initial_url
    seen = set()

    try:
        for index in range(5):
            if current_url in seen:
                result["error"] = "Redirect loop detected"
                break

            seen.add(current_url)

            response = request_once(
                current_url,
                proxy_url,
                library,
            )

            hop = inspect_response(response)
            hop["hop"] = index + 1
            result["hops"].append(hop)

            if response.status_code not in (301, 302, 303, 307, 308):
                break

            location = response.headers.get("Location")
            if not location:
                break

            current_url = urljoin(current_url, location)
            time.sleep(0.3)

    except Exception as exc:
        result["error"] = (
            f"{type(exc).__name__}: {str(exc)[:300]}"
        )

    return result


def run_diagnostics():
    proxy_url = get_proxy_url()

    targets = [
        (
            "ozon_home_direct",
            "https://www.ozon.ru/",
            None,
            "requests",
        ),
        (
            "ozon_home_proxy",
            "https://www.ozon.ru/",
            proxy_url,
            "requests",
        ),
        (
            "ozon_api_proxy",
            "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2"
            "?url=%2Fcategory%2Fbluzy-i-rubashki-zhenskie-7511%2F",
            proxy_url,
            "requests",
        ),
    ]

    results = []

    try:
        for label, url, proxy, library in targets:
            if label.endswith("_proxy") and not proxy:
                results.append({
                    "test": label,
                    "skipped": True,
                    "reason": "Proxy is not configured",
                })
                continue

            item = trace_redirects(
                label,
                url,
                proxy,
                library,
            )

            results.append(item)

            with state_lock:
                state["results"] = list(results)

            print(
                json.dumps(item, ensure_ascii=False),
                flush=True,
            )

        with state_lock:
            state["status"] = "COMPLETED"
            state["finished_at"] = now()

    except Exception as exc:
        with state_lock:
            state["status"] = "FAILED"
            state["error"] = (
                f"{type(exc).__name__}: {str(exc)[:500]}"
            )
            state["finished_at"] = now()

    finally:
        with state_lock:
            state["running"] = False


class Handler(BaseHTTPRequestHandler):

    def send_json(self, data, code=200):
        body = json.dumps(
            data,
            ensure_ascii=False,
        ).encode("utf-8")

        self.send_response(code)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            self.send_json({
                "ok": True,
                "proxy_configured": bool(get_proxy_url()),
                "curl_cffi_available": CURL_CFFI_AVAILABLE,
                "browser_required": False,
            })
            return

        if path == "/test-http":
            with state_lock:
                if state["running"]:
                    self.send_json({
                        "started": False,
                        "message": "A test is already running",
                    })
                    return

                state.update({
                    "running": True,
                    "started_at": now(),
                    "finished_at": None,
                    "status": "RUNNING",
                    "results": [],
                    "error": None,
                })

            threading.Thread(
                target=run_diagnostics,
                daemon=True,
            ).start()

            self.send_json({
                "started": True,
                "status_url": "/status",
            })
            return

        if path == "/status":
            with state_lock:
                snapshot = json.loads(
                    json.dumps(state, ensure_ascii=False)
                )

            self.send_json(snapshot)
            return

        self.send_json({
            "project": "Glukolov HTTP diagnostics",
            "endpoints": [
                "/health",
                "/test-http",
                "/status",
            ],
        })

    def log_message(self, fmt, *args):
        print(
            f"{datetime.now().isoformat()} HTTP request",
            flush=True,
        )


print(
    f"Glukolov HTTP diagnostics started on port {PORT}",
    flush=True,
)

server = ThreadingHTTPServer(
    ("0.0.0.0", PORT),
    Handler,
)

server.serve_forever()
