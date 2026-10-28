import os
import sys
import json
import time
import threading
import traceback
import subprocess

from datetime import datetime, timezone
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


PORT = int(os.getenv("PORT", "8080"))
TIMEOUT = 45000
WORKER_TIMEOUT = 120

DATA_DIR = Path(os.getenv("GLUKOLOV_DATA_DIR", "/tmp/glukolov"))
STATE_FILE = DATA_DIR / "state.json"
LOG_FILE = DATA_DIR / "camoufox.log"
PID_FILE = DATA_DIR / "worker.pid"

PROXY_HOST = os.getenv("PROXY_HOST", "").strip()
PROXY_PORT = os.getenv("PROXY_PORT", "").strip()
PROXY_USER = os.getenv("PROXY_USER", "")
PROXY_PASS = os.getenv("PROXY_PASS", "")
PROXY_URL = os.getenv("PROXY_URL", "").strip()

lock = threading.RLock()


def now():
    return datetime.now(timezone.utc).isoformat()


def ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def log(message):
    ensure_dir()
    line = f"[{now()}] {message}\n"

    with lock:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    print(line.rstrip(), flush=True)


def initial_state():
    return {
        "running": False,
        "status": "IDLE",
        "started_at": None,
        "finished_at": None,
        "stage": None,
        "proxy": proxy_info(),
        "results": [],
        "error": None,
        "worker_pid": None,
        "last_activity_at": None,
    }


def proxy_info():
    return {
        "configured": bool(
            PROXY_URL or (PROXY_HOST and PROXY_PORT)
        ),
        "host": PROXY_HOST or (
            "configured in PROXY_URL" if PROXY_URL else None
        ),
        "port": PROXY_PORT or None,
        "authentication_configured": bool(
            PROXY_USER and PROXY_PASS
        ),
        "credentials_hidden": True,
        "browser": "Camoufox",
        "protocol": "HTTP",
    }


def read_state():
    ensure_dir()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return initial_state()


def write_state(data):
    ensure_dir()
    temp_file = STATE_FILE.with_suffix(".tmp")

    with lock:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_file, STATE_FILE)


def update_state(**kwargs):
    data = read_state()
    data.update(kwargs)
    data["last_activity_at"] = now()
    write_state(data)


def set_stage(stage):
    update_state(stage=stage)
    log(f"STAGE: {stage}")


def get_proxy():
    if PROXY_URL:
        server = PROXY_URL
    else:
        if not PROXY_HOST or not PROXY_PORT:
            raise RuntimeError(
                "PROXY_HOST or PROXY_PORT is missing"
            )

        server = f"http://{PROXY_HOST}:{PROXY_PORT}"

    result = {"server": server}

    if PROXY_USER:
        result["username"] = PROXY_USER

    if PROXY_PASS:
        result["password"] = PROXY_PASS

    return result


def inspect_page(page):
    return page.evaluate("""() => {
        const body = document.body?.innerText || '';
        const html = document.documentElement?.outerHTML || '';

        const links = Array.from(
            document.querySelectorAll('a[href*="/product/"]')
        );

        const priceElements = Array.from(
            document.querySelectorAll(
                '[class*="price"], [data-widget*="price"]'
            )
        ).slice(0, 30).map(el => ({
            text: (el.innerText || '').trim().slice(0, 120),
            className: String(el.className || '').slice(0, 120)
        }));

        return {
            title: document.title,
            body_preview: body.slice(0, 1500),
            body_length: body.length,
            html_length: html.length,
            product_links: links.length,
            product_link_samples: links.slice(0, 10).map(a => ({
                text: (a.innerText || '').trim().slice(0, 120),
                href: a.href
            })),
            price_markers:
                html.includes('priceV2') ||
                html.includes('price_v2'),
            widget_states: html.includes('widgetStates'),
            tile_grid: html.includes('tileGrid'),
            price_elements: priceElements,
            challenge_text:
                /captcha|похоже, нет соединения|выключите vpn|доступ ограничен/i
                    .test(body)
        };
    }""")


def worker_main():
    ensure_dir()
    log("WORKER STARTED")

    state = read_state()
    state.update({
        "running": True,
        "status": "RUNNING",
        "stage": "worker_started",
        "worker_pid": os.getpid(),
        "last_activity_at": now(),
        "error": None,
    })
    write_state(state)

    browser = None
    context = None

    try:
        if not PROXY_URL and not (
            PROXY_HOST and PROXY_PORT
        ):
            raise RuntimeError("Proxy configuration is missing")

        set_stage("importing_camoufox")
        from camoufox.sync_api import Camoufox

        log("Camoufox import successful")

        proxy = get_proxy()

        # Пишем состояние до старта браузера, чтобы точно знать,
        # на каком этапе процесс остановился.
        set_stage("launching_camoufox")
        log("Starting Camoufox browser")

        with Camoufox(
            headless=True,
            proxy=proxy,
        ) as browser:
            log("Camoufox browser launched")

            set_stage("creating_context")

            context = browser.new_context(
                viewport={"width": 1365, "height": 900},
                locale="ru-RU",
                timezone_id="Europe/Moscow",
            )

            page = context.new_page()
            page.set_default_timeout(15000)

            result = {
                "target": "Ozon homepage through Camoufox",
                "browser": "Camoufox",
                "started_at": now(),
                "url": "https://www.ozon.ru/",
            }

            state = read_state()
            state["results"] = [result]
            write_state(state)

            set_stage("opening_ozon")
            log("Navigating to Ozon homepage")

            try:
                response = page.goto(
                    "https://www.ozon.ru/",
                    wait_until="domcontentloaded",
                    timeout=TIMEOUT,
                )

                result["http_status"] = (
                    response.status if response else None
                )
                result["final_url"] = page.url

                set_stage("waiting_for_page")
                page.wait_for_timeout(4000)

                set_stage("inspecting_page")
                result.update(inspect_page(page))

            except Exception as exc:
                result["navigation_error"] = {
                    "type": type(exc).__name__,
                    "message": str(exc)[:2000],
                    "final_url": page.url,
                }
                log(
                    "NAVIGATION ERROR: "
                    f"{type(exc).__name__}: {str(exc)[:1000]}"
                )

                try:
                    result.update(inspect_page(page))
                except Exception as inspect_exc:
                    log(
                        "INSPECTION ERROR: "
                        f"{type(inspect_exc).__name__}: "
                        f"{str(inspect_exc)[:1000]}"
                    )

            result["finished_at"] = now()

            status = result.get("http_status")
            products = result.get("product_links", 0)
            challenge = result.get("challenge_text", False)

            if status == 200 and products > 0:
                result["verdict"] = "PRODUCTS_LOADED"
            elif status == 200 and (
                result.get("price_markers")
                or result.get("widget_states")
            ):
                result["verdict"] = "PAGE_LOADED_CHECK_PRODUCT_DATA"
            elif status == 403 or challenge:
                result["verdict"] = "OZON_BLOCKED_OR_CHALLENGE"
            elif result.get("navigation_error"):
                result["verdict"] = "NAVIGATION_FAILED"
            else:
                result["verdict"] = "NO_PRODUCTS_DETECTED"

            state = read_state()
            state["results"] = [result]
            state["stage"] = "finished"
            state["status"] = "COMPLETED"
            state["running"] = False
            state["finished_at"] = now()
            state["last_activity_at"] = now()
            write_state(state)

            log(f"TEST FINISHED: {result['verdict']}")

    except BaseException as exc:
        error = {
            "type": type(exc).__name__,
            "message": str(exc)[:3000],
            "traceback": traceback.format_exc()[-6000:],
        }

        log(
            "WORKER ERROR: "
            f"{error['type']}: {error['message']}"
        )
        log(error["traceback"])

        state = read_state()
        state.update({
            "running": False,
            "status": "ERROR",
            "stage": "worker_error",
            "finished_at": now(),
            "last_activity_at": now(),
            "error": error,
        })
        write_state(state)

    finally:
        if context:
            try:
                context.close()
            except Exception as exc:
                log(f"Context close error: {exc}")

        state = read_state()

        # Не затираем ERROR, если произошла ошибка.
        if state.get("status") == "RUNNING":
            state.update({
                "running": False,
                "status": "COMPLETED",
                "stage": "worker_finished",
                "finished_at": now(),
            })
            write_state(state)

        log("WORKER EXITED")


def worker_is_running():
    state = read_state()
    pid = state.get("worker_pid")

    if not state.get("running") or not pid:
        return False

    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def start_test():
    ensure_dir()

    with lock:
        current = read_state()

        if current.get("running") and worker_is_running():
            return False

        state = {
            "running": True,
            "status": "RUNNING",
            "started_at": now(),
            "finished_at": None,
            "stage": "starting_worker",
            "proxy": proxy_info(),
            "results": [],
            "error": None,
            "worker_pid": None,
            "last_activity_at": now(),
        }
        write_state(state)

        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n[{now()}] ===== NEW TEST =====\n")
            f.flush()
            os.fsync(f.fileno())

        log("Starting separate Camoufox worker process")

        try:
            with open(LOG_FILE, "a", encoding="utf-8") as log_file:
                process = subprocess.Popen(
                    [sys.executable, os.path.abspath(__file__), "--worker"],
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )

            state = read_state()
            state["worker_pid"] = process.pid
            state["last_activity_at"] = now()
            write_state(state)

            log(f"Worker process launched, pid={process.pid}")

        except Exception as exc:
            error = {
                "type": type(exc).__name__,
                "message": str(exc)[:2000],
                "traceback": traceback.format_exc()[-4000:],
            }

            state = read_state()
            state.update({
                "running": False,
                "status": "ERROR",
                "stage": "worker_launch_failed",
                "finished_at": now(),
                "error": error,
            })
            write_state(state)
            log(f"Worker launch failed: {error}")
            return False

    return True


def read_log_tail(max_bytes=24000):
    ensure_dir()

    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            return f.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return f"Log read error: {exc}"


class Handler(BaseHTTPRequestHandler):

    def send_json(self, data, status=200):
        body = json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urlsplit(self.path).path

        if path == "/health":
            return self.send_json({
                "ok": True,
                "service": "glukolov-ozon-test",
                "proxy": proxy_info(),
                "time": now(),
            })

        if path == "/status":
            state = read_state()

            if state.get("running") and not worker_is_running():
                # Сохраняем факт неожиданного завершения процесса.
                state.update({
                    "running": False,
                    "status": "WORKER_EXITED",
                    "stage": "worker_process_not_running",
                    "finished_at": now(),
                    "last_activity_at": now(),
                })
                write_state(state)
                log("WARNING: state says running, but worker process is absent")

            return self.send_json(read_state())

        if path == "/diagnostics":
            state = read_state()
            return self.send_json({
                "state": state,
                "log_tail": read_log_tail(),
                "data_directory": str(DATA_DIR),
                "state_file_exists": STATE_FILE.exists(),
                "log_file_exists": LOG_FILE.exists(),
                "worker_pid_file_exists": PID_FILE.exists(),
            })

        if path in ("/test-camoufox", "/test-http"):
            accepted = start_test()

            if not accepted:
                return self.send_json({
                    "accepted": False,
                    "message": "Worker launch failed or test already running",
                    "status_url": "/status",
                    "diagnostics_url": "/diagnostics",
                }, 409)

            return self.send_json({
                "accepted": True,
                "message": "Camoufox worker started",
                "status_url": "/status",
                "diagnostics_url": "/diagnostics",
            }, 202)

        return self.send_json({
            "error": "not_found",
            "available": [
                "/health",
                "/status",
                "/test-camoufox",
                "/test-http",
                "/diagnostics",
            ],
        }, 404)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    ensure_dir()

    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        worker_main()
    else:
        if not STATE_FILE.exists():
            write_state(initial_state())

        print(
            "Glukolov Camoufox diagnostic started",
            flush=True,
        )

        server = ThreadingHTTPServer(
            ("0.0.0.0", PORT),
            Handler,
        )
        server.serve_forever()
