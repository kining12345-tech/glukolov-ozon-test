import base64
import json
import os
import socket
import ssl
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
OZON_HOST = "www.ozon.ru"
OZON_PORT = 443

state_lock = threading.Lock()

state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "status": "IDLE",
    "results": {},
    "error": None,
}


def now():
    return datetime.now(timezone.utc).isoformat()


def get_proxy_config():
    direct_url = os.getenv("PROXY_URL")

    if direct_url:
        parsed = urlparse(direct_url)
        return {
            "host": parsed.hostname,
            "port": parsed.port or 80,
            "user": parsed.username,
            "password": parsed.password,
            "configured": bool(parsed.hostname),
        }

    host = os.getenv("PROXY_HOST")
    port = os.getenv("PROXY_PORT")

    if not host or not port:
        return {
            "host": None,
            "port": None,
            "user": None,
            "password": None,
            "configured": False,
        }

    return {
        "host": host,
        "port": int(port),
        "user": os.getenv("PROXY_USER"),
        "password": os.getenv("PROXY_PASS"),
        "configured": True,
    }


def safe_url_path(url):
    if not url:
        return None

    try:
        parsed = urlparse(url)
        path = parsed.path or "/"

        if parsed.query:
            path += "?" + parsed.query

        return path[:1500]
    except Exception:
        return None


def resolve_dns(host):
    started = time.monotonic()

    result = {
        "host": host,
        "started_at": now(),
    }

    try:
        records = socket.getaddrinfo(
            host,
            443,
            type=socket.SOCK_STREAM,
        )

        addresses = sorted({
            item[4][0]
            for item in records
        })

        result["ok"] = True
        result["addresses"] = addresses
        result["address_count"] = len(addresses)

    except Exception as exc:
        result["ok"] = False
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)[:300]

    result["duration_ms"] = round(
        (time.monotonic() - started) * 1000,
        2,
    )
    result["finished_at"] = now()

    return result


def inspect_tls_socket(sock, host):
    cert = sock.getpeercert() or {}

    subject = []
    for group in cert.get("subject", []):
        for key, value in group:
            subject.append({
                "field": key,
                "value": value,
            })

    issuer = []
    for group in cert.get("issuer", []):
        for key, value in group:
            issuer.append({
                "field": key,
                "value": value,
            })

    return {
        "host": host,
        "tls_version": sock.version(),
        "cipher": sock.cipher(),
        "alpn": sock.selected_alpn_protocol(),
        "certificate": {
            "subject": subject,
            "issuer": issuer,
            "not_before": cert.get("notBefore"),
            "not_after": cert.get("notAfter"),
            "subject_alt_names": [
                list(item)
                for item in cert.get("subjectAltName", [])[:30]
            ],
        },
    }


def test_tls_direct():
    started = time.monotonic()

    result = {
        "test": "tls_direct",
        "host": OZON_HOST,
        "started_at": now(),
    }

    raw = None

    try:
        raw = socket.create_connection(
            (OZON_HOST, OZON_PORT),
            timeout=12,
        )
        raw.settimeout(12)

        context = ssl.create_default_context()
        context.set_alpn_protocols(["h2", "http/1.1"])

        with context.wrap_socket(
            raw,
            server_hostname=OZON_HOST,
        ) as tls_sock:
            result["ok"] = True
            result.update(
                inspect_tls_socket(tls_sock, OZON_HOST)
            )

    except Exception as exc:
        result["ok"] = False
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)[:300]

        if raw:
            try:
                raw.close()
            except Exception:
                pass

    result["duration_ms"] = round(
        (time.monotonic() - started) * 1000,
        2,
    )
    result["finished_at"] = now()

    return result


def test_tls_proxy(proxy):
    started = time.monotonic()

    result = {
        "test": "tls_proxy",
        "host": OZON_HOST,
        "proxy_configured": proxy["configured"],
        "started_at": now(),
    }

    raw = None

    if not proxy["configured"]:
        result["ok"] = False
        result["error"] = "Proxy is not configured"
        return result

    try:
        raw = socket.create_connection(
            (proxy["host"], proxy["port"]),
            timeout=12,
        )
        raw.settimeout(12)

        connect_lines = [
            f"CONNECT {OZON_HOST}:{OZON_PORT} HTTP/1.1",
            f"Host: {OZON_HOST}:{OZON_PORT}",
            "Proxy-Connection: Keep-Alive",
        ]

        if proxy["user"]:
            credentials = (
                f"{proxy['user']}:{proxy['password'] or ''}"
            ).encode("utf-8")

            token = base64.b64encode(
                credentials
            ).decode("ascii")

            connect_lines.append(
                f"Proxy-Authorization: Basic {token}"
            )

        request = (
            "\r\n".join(connect_lines)
            + "\r\n\r\n"
        ).encode("ascii")

        raw.sendall(request)

        response = b""

        while b"\r\n\r\n" not in response:
            chunk = raw.recv(1)
            if not chunk:
                break

            response += chunk

            if len(response) > 16384:
                raise RuntimeError(
                    "Proxy CONNECT response too large"
                )

        header_text = response.decode(
            "iso-8859-1",
            errors="replace",
        )

        lines = header_text.split("\r\n")
        status_line = lines[0] if lines else ""

        result["proxy_connect_status"] = status_line

        # Не выводим Proxy-Authorization или иные секреты.
        safe_headers = {}

        for line in lines[1:]:
            if ":" not in line:
                continue

            name, value = line.split(":", 1)
            key = name.strip().lower()

            if key in (
                "content-length",
                "content-type",
                "server",
                "via",
                "proxy-agent",
            ):
                safe_headers[key] = value.strip()[:300]

        result["proxy_response_headers"] = safe_headers

        parts = status_line.split()

        if len(parts) < 2 or parts[1] != "200":
            result["ok"] = False
            result["error"] = (
                "Proxy did not establish CONNECT tunnel"
            )
            return result

        context = ssl.create_default_context()
        context.set_alpn_protocols(["h2", "http/1.1"])

        with context.wrap_socket(
            raw,
            server_hostname=OZON_HOST,
        ) as tls_sock:
            result["ok"] = True
            result.update(
                inspect_tls_socket(tls_sock, OZON_HOST)
            )

    except Exception as exc:
        result["ok"] = False
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)[:300]

        if raw:
            try:
                raw.close()
            except Exception:
                pass

    finally:
        result["duration_ms"] = round(
            (time.monotonic() - started) * 1000,
            2,
        )
        result["finished_at"] = now()

    return result


def test_http(label, url, proxy_url=None, library="requests"):
    started = time.monotonic()

    result = {
        "test": label,
        "library": library,
        "proxy": "configured" if proxy_url else "direct",
        "request_path": safe_url_path(url),
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
                url,
                headers=headers,
                proxies=proxies,
                impersonate="chrome",
                timeout=15,
                allow_redirects=False,
            )
        else:
            response = requests.get(
                url,
                headers=headers,
                proxies=proxies,
                timeout=15,
                allow_redirects=False,
            )

        result["status"] = response.status_code
        result["response_path"] = safe_url_path(response.url)
        result["location"] = safe_url_path(
            response.headers.get("Location")
        )
        result["body_length"] = len(response.content)
        result["body_preview"] = response.text[:500]

        # Только диагностически полезные заголовки.
        # Значения Set-Cookie и Authorization не выводим.
        selected_headers = [
            "Server",
            "Date",
            "Content-Type",
            "Content-Length",
            "Location",
            "Via",
            "Age",
            "X-Cache",
            "X-Cache-Status",
            "X-Served-By",
            "X-Request-Id",
            "X-Trace-Id",
            "Retry-After",
            "Strict-Transport-Security",
        ]

        result["headers"] = {
            name: response.headers.get(name)
            for name in selected_headers
            if response.headers.get(name) is not None
        }

        result["set_cookie_present"] = (
            "Set-Cookie" in response.headers
        )

        lower = response.text.lower()

        result["markers"] = {
            "rr_parameter": "__rr=" in (
                response.headers.get("Location", "")
            ),
            "widget_states": "widgetStates" in response.text,
            "tile_grid": "tileGrid2" in response.text,
            "price_v2": "priceV2" in response.text,
            "captcha": "captcha" in lower,
            "challenge": "challenge" in lower,
        }

    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)[:300]

    result["duration_ms"] = round(
        (time.monotonic() - started) * 1000,
        2,
    )
    result["finished_at"] = now()

    return result


def build_proxy_url(proxy):
    if not proxy["configured"]:
        return None

    user = proxy["user"]
    password = proxy["password"]

    if user:
        auth = quote(user, safe="")

        if password:
            auth += ":" + quote(password, safe="")

        return (
            f"http://{auth}@"
            f"{proxy['host']}:{proxy['port']}"
        )

    return f"http://{proxy['host']}:{proxy['port']}"


def run_diagnostics():
    proxy = get_proxy_config()
    proxy_url = build_proxy_url(proxy)

    results = {}

    try:
        # 1. DNS
        results["dns_ozon"] = resolve_dns(OZON_HOST)

        if proxy["configured"]:
            results["dns_proxy"] = resolve_dns(
                proxy["host"]
            )

        # 2. TLS напрямую и через прокси
        results["tls_direct"] = test_tls_direct()

        results["tls_proxy"] = test_tls_proxy(proxy)

        # 3. HTTP-запросы с отключёнными редиректами
        home = "https://www.ozon.ru/"

        api = (
            "https://www.ozon.ru/"
            "api/entrypoint-api.bx/page/json/v2"
            "?url=%2Fcategory%2Fbluzy-i-rubashki-zhenskie-7511%2F"
        )

        results["http_home_direct"] = test_http(
            "http_home_direct",
            home,
        )

        results["http_home_proxy"] = test_http(
            "http_home_proxy",
            home,
            proxy_url,
        ) if proxy_url else {
            "skipped": True,
            "reason": "Proxy is not configured",
        }

        results["http_api_proxy"] = test_http(
            "http_api_proxy",
            api,
            proxy_url,
        ) if proxy_url else {
            "skipped": True,
            "reason": "Proxy is not configured",
        }

        if proxy_url and CURL_CFFI_AVAILABLE:
            results["curl_cffi_home_proxy"] = test_http(
                "curl_cffi_home_proxy",
                home,
                proxy_url,
                "curl_cffi",
            )

        with state_lock:
            state["results"] = results
            state["status"] = "COMPLETED"
            state["finished_at"] = now()

        print(
            json.dumps(results, ensure_ascii=False),
            flush=True,
        )

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
                "project": "Glukolov network diagnostics",
                "proxy_configured": get_proxy_config()["configured"],
                "curl_cffi_available": CURL_CFFI_AVAILABLE,
                "browser_required": False,
            })
            return

        if path == "/test-http":
            with state_lock:
                if state["running"]:
                    self.send_json({
                        "started": False,
                        "message": "Diagnostics already running",
                    })
                    return

                state.update({
                    "running": True,
                    "started_at": now(),
                    "finished_at": None,
                    "status": "RUNNING",
                    "results": {},
                    "error": None,
                })

            threading.Thread(
                target=run_diagnostics,
                daemon=True,
            ).start()

            self.send_json({
                "started": True,
                "message": "DNS, TLS and HTTP diagnostics started",
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
            "project": "Glukolov network diagnostics",
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
    f"Glukolov network diagnostics started on port {PORT}",
    flush=True,
)

server = ThreadingHTTPServer(
    ("0.0.0.0", PORT),
    Handler,
)

server.serve_forever()
