import os
import json
import threading
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from camoufox.sync_api import Camoufox


PORT = int(os.getenv("PORT", "8080"))
TIMEOUT = 45000

PROXY_HOST = os.getenv("PROXY_HOST", "").strip()
PROXY_PORT = os.getenv("PROXY_PORT", "").strip()
PROXY_USER = os.getenv("PROXY_USER", "")
PROXY_PASS = os.getenv("PROXY_PASS", "")
PROXY_URL = os.getenv("PROXY_URL", "").strip()

lock = threading.RLock()

state = {
    "running": False,
    "status": "IDLE",
    "started_at": None,
    "finished_at": None,
    "stage": None,
    "proxy": {},
    "results": [],
    "error": None,
}


def now():
    return datetime.now(timezone.utc).isoformat()


def proxy_info():
    return {
        "configured": bool(
            PROXY_URL or (PROXY_HOST and PROXY_PORT)
        ),
        "host": PROXY_HOST or (
            "configured in PROXY_URL" if PROXY_URL else None
        ),
        "port": PROXY_PORT or
