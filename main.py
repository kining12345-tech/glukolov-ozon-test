import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, quote

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


def safe_location(value):
    """Возвращает путь и параметры URL без домена и учётных данных."""
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


def inspect_response(response):
    body = response.text[:50000]
    lower = body.lower()
    location = response.headers.get("Location")

    return {
        "status": response.status_code,
        "request_url_path": safe_location(response.request.url),
        "final_url_path": safe_location(response.url),
        "location_header": safe_location(location),
        "content_type": response.headers.get("Content-Type"),
        "server": response.headers.get("Server"),
        "body_length": len(response.content),
        "body_preview": body[:1200],
        "markers": {
            "antibot": "antibot" in lower,
            "challenge": "challenge" in lower,
            "captcha": "captcha" in lower,
            "incident_id": "incidentid" in lower,
            "rr_redirect": (
                "__rr=1" in (location or "")
                or "__rr=1" in lower
            ),
            "widget_states": "widgetStates" in body,
            "tile_grid": "tileGrid2" in body,
            "price_v2": "priceV2" in body,
            "challenge_url": "challengeurl" in lower,
        },
    }


def perform_request(label, library, proxy_url, target):
    result = {
        "test": label,
        "library": library,
        "proxy": "configured" if proxy_url else "direct",
        "target": target["name"],
        "started_at": now(),
    }

    headers = {
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

    proxies = None
    if proxy_url:
        proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }

    try:
        if library == "curl_cffi":
            if not CURL_CFFI_AVAILABLE:
                raise RuntimeError("curl_cffi is not installed")

            response = curl_requests.get(
                target["url"],
                params=target.get("params"),
                headers=headers,
                proxies=proxies,
                impersonate="chrome",
                timeout=20,
                allow_redirects=False,
            )
        else:
            response = requests.get(
                target["url"],
                params=target.get("params"),
                headers=headers,
                proxies=proxies,
                timeout=20,
                allow_redirects=False,
            )

        result.update(inspect_response(response))

        if target["name"] == "ipify" and response.ok:
            try:
                result["external_ip"] = response.json().get("ip")
            except Exception:
                result["external_ip"] = "Could not parse IP"

    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)[:500]

    result["finished_at"] = now()
    return result


def run_diagnostics():
    proxy_url = get_proxy_url()

    targets = [
        {
            "name": "ipify",
            "url": "https://api.ipify.org/",
            "params": {"format": "json"},
        },
        {
            "name": "ozon_home",
            "url": "https://www.ozon.ru/",
        },
        {
            "name": "ozon_catalog_api",
            "url": (
                "https://www.ozon.ru/"
                "api/entrypoint-api.bx/page/json/v2"
            ),
            "params": {
                "url": "/category/bluzy-i-rubashki-zhenskie-7511/"
            },
        },
    ]

    plans = [
        ("requests_direct", "requests", None),
        ("requests_proxy", "requests", proxy_url),
        ("curl_cffi_proxy", "curl_cffi", proxy_url),
    ]

    results = []

    try:
        for label, library, proxy in plans:
            if label.endswith("_proxy") and not proxy:
                results.append({
                    "test": label,
                    "skipped": True,
                    "reason": "Proxy is not configured",
                })
                continue

            for target in targets:
                item = perform_request(
                    label,
                    library,
                    proxy,
                    target,
                )

                results.append(item)

                with state_lock:
                    state["results"] = list(results)

                # Не выводим полный URL запроса в логах:
                # он может содержать параметры сессии.
                print(
                    json.dumps(item, ensure_ascii=False),
                    flush=True,
                )

                time.sleep(1)

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
                "project": "Glukolov HTTP diagnostics",
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

            thread = threading.Thread(
                target=run_diagnostics,
                daemon=True,
            )
            thread.start()

            self.send_json({
                "started": True,
                "message": "HTTP diagnostics started",
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
        # Не выводим URL целиком, чтобы не раскрывать параметры.
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
