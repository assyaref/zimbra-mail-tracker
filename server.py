"""
Zimbra Mail Tracker — Standalone Python Stdlib Server (ZIMMON-style)
Zero external dependencies. Runs directly via python3 or systemd service.
"""

import os
import io
import csv
import ssl
import json
import time
import base64
import hashlib
import secrets
import mimetypes
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import config
from parser import parse_log_content, parse_log_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONF_PATH = os.getenv("ZMT_CONFIG", "/etc/zimbra-mail-tracker/config.json")

# ── Runtime Config ────────────────────────────────────────────────────────────
_runtime_cfg = {
    "host": config.HOST,
    "port": int(os.getenv("ZMT_PORT", 9444 if os.path.exists(CONF_PATH) else config.PORT)),
    "log_dir": config.LOG_DIR,
    "cert": "",
    "key": "",
    "username": "",
    "password_hash": "",
    "auto_load_interval": 60,
}

if os.path.isfile(CONF_PATH):
    try:
        with open(CONF_PATH, "r", encoding="utf-8") as f:
            _runtime_cfg.update(json.load(f))
    except Exception as e:
        print(f"[WARN] Failed to load {CONF_PATH}: {e}")

# ── In-memory Log Cache ───────────────────────────────────────────────────────
_log_cache = []
_last_auto_load = 0.0
_manual_upload = False

DEMO_DATA = [
    {"message_id": "1xlzun-00ArLt-PK", "queue_id": "A1B2C3D4E", "timestamp": "2026-10-20 09:49:00",
     "auth_domain": "zimbra.cloudmatika.com", "auth_username": "yuriko.fitria@radiant-utama.com",
     "identity": "yuriko.fitria@radiant-utama.com", "from": "yuriko.fitria@radiant-utama.com",
     "to": "mhadi16@slb.com", "status": "delivered", "relay": "mail.slb.com",
     "reason": "", "size": 48234, "subject": "Invoice Hotel Susuka dan Hotel Grand Zuri",
     "main_class": "not-spam"},
    {"message_id": "1xlzun-00ArLt-PK", "queue_id": "A1B2C3D4E", "timestamp": "2026-10-20 09:49:00",
     "auth_domain": "zimbra.cloudmatika.com", "auth_username": "yuriko.fitria@radiant-utama.com",
     "identity": "yuriko.fitria@radiant-utama.com", "from": "yuriko.fitria@radiant-utama.com",
     "to": "daniellahm07@gmail.com", "status": "delivered", "relay": "gmail-smtp-in.l.google.com",
     "reason": "", "size": 48234, "subject": "Invoice Hotel Susuka dan Hotel Grand Zuri",
     "main_class": "not-spam"},
    {"message_id": "1xJ03z-006pT8-9C", "queue_id": "B2C3D4E5F", "timestamp": "2026-10-20 09:45:00",
     "auth_domain": "zimbra.cloudmatika.com", "auth_username": "yuriko.fitria@radiant-utama.com",
     "identity": "yuriko.fitria@radiant-utama.com", "from": "yuriko.fitria@radiant-utama.com",
     "to": "nali56@slb.com", "status": "delivered", "relay": "mail.slb.com",
     "reason": "", "size": 32100, "subject": "FW: [Ext] RE: Q4 OPEN GOPool Recruitment - October 2026",
     "main_class": "not-spam"},
    {"message_id": "1xJ03z-006pT8-9C", "queue_id": "B2C3D4E5F", "timestamp": "2026-10-20 09:45:00",
     "auth_domain": "zimbra.cloudmatika.com", "auth_username": "yuriko.fitria@radiant-utama.com",
     "identity": "yuriko.fitria@radiant-utama.com", "from": "yuriko.fitria@radiant-utama.com",
     "to": "mzaki16@slb.com", "status": "delivered", "relay": "mail.slb.com",
     "reason": "", "size": 32100, "subject": "FW: [Ext] RE: Q4 OPEN GOPool Recruitment - October 2026",
     "main_class": "not-spam"},
    {"message_id": "1xJ03z-006pT8-9C", "queue_id": "B2C3D4E5F", "timestamp": "2026-10-20 09:45:00",
     "auth_domain": "zimbra.cloudmatika.com", "auth_username": "yuriko.fitria@radiant-utama.com",
     "identity": "yuriko.fitria@radiant-utama.com", "from": "yuriko.fitria@radiant-utama.com",
     "to": "lqadhayna@slb.com", "status": "bounced", "relay": "",
     "reason": "550 5.1.1 User unknown", "size": 32100,
     "subject": "FW: [Ext] RE: Q4 OPEN GOPool Recruitment - October 2026",
     "main_class": "not-spam"},
    {"message_id": "1xJ03z-006pT8-9C", "queue_id": "B2C3D4E5F", "timestamp": "2026-10-20 09:45:00",
     "auth_domain": "zimbra.cloudmatika.com", "auth_username": "yuriko.fitria@radiant-utama.com",
     "identity": "yuriko.fitria@radiant-utama.com", "from": "yuriko.fitria@radiant-utama.com",
     "to": "daniellahm07@gmail.com", "status": "delivered", "relay": "gmail-smtp-in.l.google.com",
     "reason": "", "size": 32100, "subject": "FW: [Ext] RE: Q4 OPEN GOPool Recruitment - October 2026",
     "main_class": "not-spam"},
    {"message_id": "1xJ03z-006pT8-9C", "queue_id": "B2C3D4E5F", "timestamp": "2026-10-20 09:45:00",
     "auth_domain": "zimbra.cloudmatika.com", "auth_username": "yuriko.fitria@radiant-utama.com",
     "identity": "yuriko.fitria@radiant-utama.com", "from": "yuriko.fitria@radiant-utama.com",
     "to": "crosyidah@slb.com", "status": "deferred", "relay": "mail.slb.com",
     "reason": "451 Temporary failure", "size": 32100,
     "subject": "FW: [Ext] RE: Q4 OPEN GOPool Recruitment - October 2026",
     "main_class": "not-spam"},
    {"message_id": "1xlylt-006Zfm-14", "queue_id": "C3D4E5F6G", "timestamp": "2026-10-20 08:22:00",
     "auth_domain": "zimbra.cloudmatika.com", "auth_username": "admin@radiant-utama.com",
     "identity": "admin@radiant-utama.com", "from": "admin@radiant-utama.com",
     "to": "rakbar5@slb.com", "status": "delivered", "relay": "mail.slb.com",
     "reason": "", "size": 15200, "subject": "RE: Surat Rujukan - Bp. Alimar",
     "main_class": "not-spam"},
    {"message_id": "1xlylt-006Zfm-14", "queue_id": "C3D4E5F6G", "timestamp": "2026-10-20 08:22:00",
     "auth_domain": "zimbra.cloudmatika.com", "auth_username": "admin@radiant-utama.com",
     "identity": "admin@radiant-utama.com", "from": "admin@radiant-utama.com",
     "to": "nayondra@slb.com", "status": "delivered", "relay": "mail.slb.com",
     "reason": "", "size": 15200, "subject": "RE: Surat Rujukan - Bp. Alimar",
     "main_class": "not-spam"},
    {"message_id": "SPAM-9fXz01-spam", "queue_id": "D4E5F6G7H", "timestamp": "2026-10-20 07:10:00",
     "auth_domain": "unknown.ru", "auth_username": "bulk@unknown.ru",
     "identity": "bulk@unknown.ru", "from": "bulk@unknown.ru",
     "to": "admin@radiant-utama.com", "status": "bounced", "relay": "",
     "reason": "550 Spam detected", "size": 2048,
     "subject": "You won a prize! Claim now!", "main_class": "spam"},
]


def _maybe_auto_load():
    """Auto-load logs from log_dir on Zimbra VPS if not manually overridden."""
    global _log_cache, _last_auto_load
    if _manual_upload:
        return
    interval = int(_runtime_cfg.get("auto_load_interval", 60))
    now = time.time()
    if _log_cache and (now - _last_auto_load) < interval:
        return
    log_dir = _runtime_cfg.get("log_dir", "/var/log")
    if os.path.isdir(log_dir):
        try:
            records = parse_log_directory(log_dir)
            if records:
                _log_cache = records
        except Exception as e:
            print(f"[WARN] Auto-load failed for {log_dir}: {e}")
    _last_auto_load = now


def _build_chart(data):
    days = {}
    for i in range(6, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        days[d] = {"date": d, "delivered": 0, "bounced": 0, "deferred": 0, "spam": 0}

    for row in data:
        ts = row.get("timestamp", "")
        date = ts[:10] if ts else ""
        if date in days:
            status = row.get("status", "")
            if status in ("delivered", "sent"):
                days[date]["delivered"] += 1
            elif status == "bounced":
                days[date]["bounced"] += 1
            elif status == "deferred":
                days[date]["deferred"] += 1
            if row.get("main_class") == "spam":
                days[date]["spam"] += 1
    return list(days.values())


def _parse_multipart_file(content_type: str, body: bytes):
    """Parse multipart/form-data upload using stdlib without deprecated cgi module."""
    boundary_marker = "boundary="
    if boundary_marker not in content_type:
        return None, None
    boundary = content_type.split(boundary_marker, 1)[1].strip().strip('"').encode("utf-8")
    parts = body.split(b"--" + boundary)
    for part in parts:
        if b"Content-Disposition" not in part:
            continue
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        headers_raw = part[:header_end].decode("utf-8", errors="ignore")
        payload = part[header_end + 4:]
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        if 'name="file"' in headers_raw:
            fname = "uploaded.log"
            for token in headers_raw.split(";"):
                token = token.strip()
                if token.startswith("filename="):
                    fname = token.split("=", 1)[1].strip().strip('"')
                    break
            return fname, payload
    return None, None


class TrackerHandler(BaseHTTPRequestHandler):
    server_version = "ZMT-Intel/2.0"

    def log_message(self, format, *args):
        # Keep stdout clean for systemd journal
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {self.address_string()} - {format % args}")

    def _check_auth(self) -> bool:
        expected_user = _runtime_cfg.get("username", "")
        expected_hash = _runtime_cfg.get("password_hash", "")
        if not expected_user or not expected_hash:
            return True

        auth_hdr = self.headers.get("Authorization", "")
        if auth_hdr.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_hdr[6:]).decode("utf-8")
                user, pwd = decoded.split(":", 1)
                pwd_hash = hashlib.sha256(pwd.encode("utf-8")).hexdigest()
                if secrets.compare_digest(user, expected_user) and secrets.compare_digest(pwd_hash, expected_hash):
                    return True
            except Exception:
                pass

        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="ZIMBRA MAIL TRACKER — AUTHORIZED ACCESS ONLY"')
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"401 Unauthorized")
        return False

    def _send_json(self, obj, status=200):
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_bytes(self, data: bytes, content_type: str, status=200, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._check_auth():
            return

        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        def qget(key, default=""):
            vals = qs.get(key)
            return vals[0] if vals else default

        # ── Index HTML ────────────────────────────────────────────────────────
        if path == "/" or path == "/index.html":
            tpl_path = os.path.join(BASE_DIR, "templates", "index.html")
            with open(tpl_path, "r", encoding="utf-8") as f:
                html = f.read()
            replacements = {
                "{{ config.APP_NAME }}": config.APP_NAME,
                "{{ config.APP_SUBTITLE }}": config.APP_SUBTITLE,
                "{{ config.CLASSIFICATION }}": config.CLASSIFICATION,
                "{{ config.APP_VERSION }}": config.APP_VERSION,
                "{{ config.AGENCY_CODE }}": config.AGENCY_CODE,
            }
            for k, v in replacements.items():
                html = html.replace(k, v)
            self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8")
            return

        # ── Static files ──────────────────────────────────────────────────────
        if path.startswith("/static/"):
            rel = path.lstrip("/").replace("/", os.sep)
            full_path = os.path.abspath(os.path.join(BASE_DIR, rel))
            static_root = os.path.abspath(os.path.join(BASE_DIR, "static"))
            if not full_path.startswith(static_root) or not os.path.isfile(full_path):
                self._send_json({"error": "Not found"}, 404)
                return
            ctype, _ = mimetypes.guess_type(full_path)
            with open(full_path, "rb") as f:
                self._send_bytes(f.read(), ctype or "application/octet-stream")
            return

        # ── Health / Session endpoint (like zimmon) ───────────────────────────
        if path == "/api/session":
            version_file = os.path.join(BASE_DIR, "VERSION")
            ver = open(version_file).read().strip() if os.path.isfile(version_file) else config.APP_VERSION
            self._send_json({"status": "online", "version": ver, "app": config.APP_NAME})
            return

        # ── GET /api/logs ─────────────────────────────────────────────────────
        if path == "/api/logs":
            _maybe_auto_load()
            data = _log_cache if _log_cache else DEMO_DATA

            q = qget("q").strip().lower()
            status_f = qget("status").lower()
            main_class_f = qget("main_class").lower()
            date_from = qget("date_from")
            date_to = qget("date_to")
            domain_f = qget("auth_domain").lower()

            filtered = []
            for row in data:
                if q and not any(q in str(v).lower() for v in row.values()):
                    continue
                if status_f and row.get("status", "") != status_f:
                    continue
                if main_class_f and row.get("main_class", "") != main_class_f:
                    continue
                if domain_f and domain_f not in row.get("auth_domain", "").lower():
                    continue
                if date_from and row.get("timestamp", "") < date_from:
                    continue
                if date_to and row.get("timestamp", "") > date_to + " 23:59:59":
                    continue
                filtered.append(row)

            sort_by = qget("sort", "timestamp")
            sort_dir = qget("dir", "desc")
            reverse = sort_dir == "desc"
            valid_cols = {
                "message_id", "timestamp", "auth_domain", "auth_username",
                "identity", "from", "to", "main_class", "subject", "status", "size",
            }
            if sort_by in valid_cols:
                filtered.sort(key=lambda x: str(x.get(sort_by, "")), reverse=reverse)

            total = len(filtered)
            stats = {
                "total": total,
                "delivered": sum(1 for r in filtered if r.get("status") == "delivered"),
                "sent": sum(1 for r in filtered if r.get("status") == "sent"),
                "bounced": sum(1 for r in filtered if r.get("status") == "bounced"),
                "deferred": sum(1 for r in filtered if r.get("status") == "deferred"),
                "spam": sum(1 for r in filtered if r.get("main_class") == "spam"),
            }
            chart = _build_chart(filtered)

            try:
                page = max(1, int(qget("page", "1")))
            except ValueError:
                page = 1
            try:
                per_page = min(500, max(1, int(qget("per_page", "50"))))
            except ValueError:
                per_page = 50

            total_pages = max(1, (total + per_page - 1) // per_page)
            page = min(page, total_pages)
            start = (page - 1) * per_page
            rows = filtered[start:start + per_page]

            self._send_json({
                "rows": rows,
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages,
                "stats": stats,
                "chart": chart,
                "is_demo": not bool(_log_cache),
            })
            return

        # ── GET /api/export/csv ───────────────────────────────────────────────
        if path == "/api/export/csv":
            _maybe_auto_load()
            data = _log_cache if _log_cache else DEMO_DATA
            output = io.StringIO()
            cols = [
                "message_id", "timestamp", "auth_domain", "auth_username",
                "identity", "from", "to", "main_class", "subject", "status",
                "relay", "reason", "size", "queue_id",
            ]
            writer = csv.DictWriter(output, fieldnames=cols, extrasaction="ignore")
            writer.writeheader()
            for row in data:
                writer.writerow(row)
            fname = f"mail_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            self._send_bytes(
                output.getvalue().encode("utf-8"),
                "text/csv; charset=utf-8",
                extra_headers={"Content-Disposition": f'attachment; filename="{fname}"'},
            )
            return

        # ── GET /api/stats/summary ────────────────────────────────────────────
        if path == "/api/stats/summary":
            _maybe_auto_load()
            data = _log_cache if _log_cache else DEMO_DATA
            self._send_json(_build_chart(data))
            return

        self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        global _log_cache, _manual_upload
        if not self._check_auth():
            return

        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        if length > 100 * 1024 * 1024:
            self._send_json({"error": "Payload too large (max 100MB)"}, 413)
            return
        body = self.rfile.read(length) if length > 0 else b""

        # ── POST /api/upload ──────────────────────────────────────────────────
        if path == "/api/upload":
            ctype = self.headers.get("Content-Type", "")
            fname, payload = _parse_multipart_file(ctype, body)
            if not payload:
                self._send_json({"error": "No file uploaded"}, 400)
                return
            try:
                content = payload.decode("utf-8", errors="ignore")
                records = parse_log_content(content)
                _log_cache = records
                _manual_upload = True
                self._send_json({"success": True, "count": len(records), "filename": fname})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return

        # ── POST /api/load-dir ────────────────────────────────────────────────
        if path == "/api/load-dir":
            try:
                req = json.loads(body.decode("utf-8")) if body else {}
            except Exception:
                req = {}
            log_dir = req.get("path", _runtime_cfg.get("log_dir", config.LOG_DIR))
            if not os.path.isdir(log_dir):
                self._send_json({"error": f"Directory not found: {log_dir}"}, 404)
                return
            try:
                records = parse_log_directory(log_dir)
                _log_cache = records
                _manual_upload = False
                self._send_json({"success": True, "count": len(records), "path": log_dir})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return

        # ── POST /api/clear ───────────────────────────────────────────────────
        if path == "/api/clear":
            _log_cache = []
            _manual_upload = True
            self._send_json({"success": True})
            return

        self._send_json({"error": "Not found"}, 404)


def run_server():
    host = _runtime_cfg.get("host", "0.0.0.0")
    port = int(_runtime_cfg.get("port", 9444))
    httpd = ThreadingHTTPServer((host, port), TrackerHandler)

    cert = _runtime_cfg.get("cert", "")
    key = _runtime_cfg.get("key", "")
    proto = "http"
    if cert and key and os.path.isfile(cert) and os.path.isfile(key):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        proto = "https"

    print(f"[*] {config.APP_NAME} running at {proto}://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Shutting down...")
        httpd.server_close()


if __name__ == "__main__":
    run_server()
