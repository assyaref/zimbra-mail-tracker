"""
Zimbra Mail Tracker — Standalone Python Server with Persistent DB & Background Log Sync
Zero external dependencies. Runs via standard library.
"""

import os
import io
import csv
import ssl
import sys
import json
import time
import base64
import hashlib
import secrets
import threading
import traceback
import mimetypes
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import config
from parser import parse_log_content, stream_parse_log
import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_TLS_ENABLED = False
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
    "auto_load_interval": 30,
}

if os.path.isfile(CONF_PATH):
    try:
        with open(CONF_PATH, "r", encoding="utf-8") as f:
            _runtime_cfg.update(json.load(f))
    except Exception as e:
        print(f"[WARN] Failed to load {CONF_PATH}: {e}")

# Inisialisasi Database SQLite
db.init_db()

# ── ZMT Doctor report channel ────────────────────────────────────────────────
_DOCTOR_DIR = os.path.dirname(CONF_PATH)
_DOCTOR_TOKEN_PATH = os.getenv("ZMT_DOCTOR_TOKEN_FILE", os.path.join(_DOCTOR_DIR, "doctor.token"))
_DOCTOR_REPORT_PATH = os.getenv("ZMT_DOCTOR_REPORT_FILE", os.path.join(_DOCTOR_DIR, "doctor-report.json"))

def _load_or_create_doctor_token():
    try:
        os.makedirs(_DOCTOR_DIR, exist_ok=True)
        if os.path.isfile(_DOCTOR_TOKEN_PATH):
            with open(_DOCTOR_TOKEN_PATH, "r", encoding="utf-8") as token_file:
                token = token_file.read().strip()
            if token:
                return token
        token = secrets.token_urlsafe(48)
        fd = os.open(_DOCTOR_TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as token_file:
            token_file.write(token + "\n")
        return token
    except OSError as exc:
        print(f"[WARN] ZMT Doctor token unavailable: {exc}")
        return ""

_DOCTOR_TOKEN = _load_or_create_doctor_token()

def _read_doctor_report():
    try:
        with open(_DOCTOR_REPORT_PATH, "r", encoding="utf-8") as report_file:
            return json.load(report_file)
    except (OSError, ValueError):
        return None

def _save_doctor_report(report):
    os.makedirs(os.path.dirname(_DOCTOR_REPORT_PATH), exist_ok=True)
    temp_path = _DOCTOR_REPORT_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as report_file:
        json.dump(report, report_file, ensure_ascii=False)
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, _DOCTOR_REPORT_PATH)


# ── Sessions ──────────────────────────────────────────────────────────────────
SESSION_TTL = int(os.getenv("ZMT_SESSION_TTL", 12 * 3600))
_sessions = {}            # token -> expiry epoch
_sessions_lock = threading.Lock()

def _create_session():
    token = secrets.token_hex(32)
    now = time.time()
    with _sessions_lock:
        for t, exp in list(_sessions.items()):
            if exp < now:
                del _sessions[t]
        _sessions[token] = now + SESSION_TTL
    return token

def _valid_session(token):
    with _sessions_lock:
        exp = _sessions.get(token)
        if exp is None:
            return False
        if exp < time.time():
            del _sessions[token]
            return False
        return True

def _drop_session(token):
    with _sessions_lock:
        _sessions.pop(token, None)

# ── Password hashing (PBKDF2; hash SHA-256 lama tetap diterima & di-upgrade) ──
PBKDF2_ITER = 260000

def _hash_password(pwd):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt, PBKDF2_ITER)
    return f"pbkdf2_sha256${PBKDF2_ITER}${salt.hex()}${dk.hex()}"

def _verify_password(pwd, stored):
    if not stored:
        return False
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iters, salt_hex, hash_hex = stored.split("$", 3)
            dk = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), bytes.fromhex(salt_hex), int(iters))
            return secrets.compare_digest(dk.hex(), hash_hex)
        except (ValueError, TypeError):
            return False
    return secrets.compare_digest(hashlib.sha256(pwd.encode("utf-8")).hexdigest(), stored)

def _upgrade_password_hash(pwd):
    """Ganti hash SHA-256 tanpa salt menjadi PBKDF2 setelah login sukses."""
    if _runtime_cfg.get("password_hash", "").startswith("pbkdf2_sha256$") or not os.path.isfile(CONF_PATH):
        return
    try:
        with open(CONF_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["password_hash"] = _hash_password(pwd)
        tmp = CONF_PATH + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, CONF_PATH)
        _runtime_cfg["password_hash"] = data["password_hash"]
        print("[INFO] password_hash upgraded to PBKDF2")
    except Exception as exc:
        print(f"[WARN] password hash upgrade failed: {exc}")

# ── Login rate limit ──────────────────────────────────────────────────────────
_LOGIN_WINDOW = 900
_LOGIN_MAX_FAIL = 10
_login_fail = {}
_login_lock = threading.Lock()

def _login_blocked(ip):
    now = time.time()
    with _login_lock:
        hits = [t for t in _login_fail.get(ip, []) if now - t < _LOGIN_WINDOW]
        _login_fail[ip] = hits
        return len(hits) >= _LOGIN_MAX_FAIL

def _login_failed(ip):
    with _login_lock:
        _login_fail.setdefault(ip, []).append(time.time())

MAX_BODY = 100 * 1024 * 1024

# ── Background Worker: Log Synchronizer ───────────────────────────────────────
_sync_status = {"running": False, "last_sync": "Never", "files_processed": 0, "total_records": 0,
                "last_error": "", "last_duration_s": 0, "last_enrichment": {}}

LOG_PREFIXES = ("zimbra.log", "maillog", "mail.log")

def _list_log_files(log_dir):
    files = []
    for fname in os.listdir(log_dir):
        if fname.startswith(LOG_PREFIXES):
            full_p = os.path.join(log_dir, fname)
            if os.path.isfile(full_p):
                files.append(full_p)
    # Urutkan dari file lama ke file baru agar file hasil rotasi diproses lebih dulu
    files.sort(key=lambda x: os.path.getmtime(x))
    return files

def sync_logs_once(log_dir):
    """Satu siklus sinkronisasi semua log. Return (files_processed, records_touched)."""
    conn = db.get_db()
    processed = 0
    touched = 0
    try:
        for fpath in _list_log_files(log_dir):
            try:
                st = os.stat(fpath)
                is_gz = fpath.endswith(".gz")
                if is_gz:
                    row = conn.execute("SELECT last_mtime FROM file_offsets WHERE filepath = ?", (fpath,)).fetchone()
                    if row and row["last_mtime"] == st.st_mtime:
                        continue
                    start = 0
                else:
                    start = db.get_offset(conn, fpath, st.st_ino, st.st_size)
                    if start == st.st_size:
                        db.set_offset(conn, fpath, start, st.st_mtime, st.st_ino)
                        conn.commit()
                        continue

                sink = {}
                new_pos = start
                for batch, pos in stream_parse_log(fpath, start_offset=start, meta_sink=sink):
                    if batch:
                        touched += db.insert_records(batch)
                    new_pos = pos
                db.upsert_queue_meta(sink)
                db.set_offset(conn, fpath, 0 if is_gz else new_pos, st.st_mtime, st.st_ino)
                conn.commit()
                processed += 1
            except Exception as e:
                _sync_status["last_error"] = f"{os.path.basename(fpath)}: {e}"
                print(f"[ERR] sync {fpath}: {e}")
                traceback.print_exc()
    finally:
        conn.close()
    _sync_status["last_enrichment"] = {k: v for k, v in db.enrich_from_meta().items() if v}
    return processed, touched

def _background_log_sync():
    """Background thread yang membaca dan mengimpor SEMUA log mail secara terus menerus."""
    time.sleep(2)  # Delay start
    while True:
        try:
            log_dir = _runtime_cfg.get("log_dir", "/var/log")
            if os.path.isdir(log_dir):
                _sync_status["running"] = True
                t0 = time.time()
                processed, touched = sync_logs_once(log_dir)
                _sync_status["files_processed"] = processed
                _sync_status["total_records"] = touched
                _sync_status["last_duration_s"] = round(time.time() - t0, 2)
                _sync_status["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            _sync_status["last_error"] = str(e)
            print(f"[ERR] Background log sync: {e}")
            traceback.print_exc()
        finally:
            _sync_status["running"] = False

        interval = int(_runtime_cfg.get("auto_load_interval", 30))
        time.sleep(max(10, interval))

# Start Worker Thread (dapat dimatikan untuk tool offline/pengujian)
if os.getenv("ZMT_NO_SYNC", "") != "1":
    sync_thread = threading.Thread(target=_background_log_sync, daemon=True)
    sync_thread.start()


class TrackerHandler(BaseHTTPRequestHandler):
    server_version = "ZMT-Intel/2.0"
    sys_version = ""
    timeout = 60

    def log_message(self, format, *args):
        pass

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if _TLS_ENABLED:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        super().end_headers()

    def _session_cookie(self, token):
        secure = "; Secure" if _TLS_ENABLED else ""
        return f"zmt_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}{secure}"

    def _cookie_token(self):
        for part in self.headers.get("Cookie", "").split(";"):
            part = part.strip()
            if part.startswith("zmt_session="):
                return part.split("=", 1)[1]
        return ""

    def _check_auth(self) -> bool:
        expected_user = _runtime_cfg.get("username", "")
        expected_hash = _runtime_cfg.get("password_hash", "")
        if not expected_user or not expected_hash:
            return True

        token = self._cookie_token()
        if token and _valid_session(token):
            return True

        auth_hdr = self.headers.get("Authorization", "")
        if auth_hdr.startswith("Basic "):
            ip = self.client_address[0]
            if _login_blocked(ip):
                return False
            try:
                decoded = base64.b64decode(auth_hdr[6:]).decode("utf-8")
                user, pwd = decoded.split(":", 1)
                if secrets.compare_digest(user, expected_user) and _verify_password(pwd, expected_hash):
                    return True
                _login_failed(ip)
            except Exception:
                _login_failed(ip)
        return False

    def _send_json(self, obj, status=200, extra_headers=None):
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
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
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        def qget(key, default=""):
            vals = qs.get(key)
            return vals[0] if vals else default

        # Static assets
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

        # Login page
        if path == "/login" or path == "/login.html":
            tpl_path = os.path.join(BASE_DIR, "templates", "login.html")
            with open(tpl_path, "r", encoding="utf-8") as f:
                html = f.read()
            self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8")
            return

        # Logout
        if path == "/logout":
            _drop_session(self._cookie_token())
            self.send_response(302)
            self.send_header("Set-Cookie", "zmt_session=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT")
            self.send_header("Location", "/login")
            self.end_headers()
            return

        # Auth check
        if not self._check_auth():
            if path.startswith("/api/"):
                self._send_json({"error": "Unauthorized"}, 401)
            else:
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
            return

        # Index page
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

        # Session
        if path == "/api/session":
            version_file = os.path.join(BASE_DIR, "VERSION")
            ver = open(version_file).read().strip() if os.path.isfile(version_file) else config.APP_VERSION
            self._send_json({"status": "online", "version": ver, "sync": _sync_status})
            return

        # Latest VPS diagnostic report (dashboard session required).
        if path == "/api/doctor/latest":
            self._send_json({"report": _read_doctor_report()})
            return

        # Vitals
        if path == "/api/vitals":
            vitals = {"cpu": 15.0, "ram_used": "6.2G", "ram_total": "16.0G", "ram_pct": 38,
                      "disk_free": "116G", "disk_pct": 45, "swap_used": "0.1G", "swap_total": "8.0G", "swap_pct": 2}
            try:
                if os.path.exists("/proc/loadavg"):
                    with open("/proc/loadavg") as f:
                        vitals["cpu"] = round(float(f.read().split()[0]) * 10, 1)
                if os.path.exists("/proc/meminfo"):
                    mem = {}
                    with open("/proc/meminfo") as f:
                        for l in f:
                            p = l.split(":")
                            if len(p) == 2:
                                mem[p[0].strip()] = int(p[1].split()[0])
                    total = mem.get("MemTotal", 1)
                    free = mem.get("MemAvailable", mem.get("MemFree", 0))
                    used = total - free
                    vitals["ram_used"] = f"{used/1048576:.1f}G"
                    vitals["ram_total"] = f"{total/1048576:.1f}G"
                    vitals["ram_pct"] = int((used / total) * 100)
                    sw_total = mem.get("SwapTotal", 1)
                    sw_free = mem.get("SwapFree", 0)
                    sw_used = max(0, sw_total - sw_free)
                    vitals["swap_used"] = f"{sw_used/1048576:.1f}G"
                    vitals["swap_total"] = f"{sw_total/1048576:.1f}G"
                    vitals["swap_pct"] = int((sw_used / max(1, sw_total)) * 100)
                st = os.statvfs("/")
                free_gb = (st.f_bavail * st.f_frsize) / (1024**3)
                total_gb = (st.f_blocks * st.f_frsize) / (1024**3)
                vitals["disk_free"] = f"{free_gb:.1f}G free"
                vitals["disk_pct"] = int(((total_gb - free_gb) / max(1, total_gb)) * 100)
            except Exception:
                pass
            self._send_json(vitals)
            return

        # Logs query from SQLite
        if path == "/api/logs":
            q = qget("q").strip()
            status_f = qget("status").lower()
            main_class_f = qget("main_class").lower()
            domain_f = qget("auth_domain").lower()
            date_from = qget("date_from")
            date_to = qget("date_to")
            sort_by = qget("sort", "timestamp")
            sort_dir = qget("dir", "desc")

            try:
                page = max(1, int(qget("page", "1")))
                per_page = min(500, max(1, int(qget("per_page", "50"))))
            except ValueError:
                page, per_page = 1, 50

            rows, total, stats = db.query_emails(
                q=q, status=status_f, main_class=main_class_f, auth_domain=domain_f,
                date_from=date_from, date_to=date_to, sort_col=sort_by, sort_dir=sort_dir,
                page=page, per_page=per_page,
                include_internal=qget("include_internal") in ("1", "true", "yes"),
            )

            total_pages = max(1, (total + per_page - 1) // per_page)
            self._send_json({
                "rows": rows,
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages,
                "stats": stats,
                "chart": [],
                "is_demo": total == 0,
            })
            return

        # Export CSV
        if path == "/api/export/csv":
            rows, _, _ = db.query_emails(
                q=qget("q").strip(), status=qget("status").lower(), main_class=qget("main_class").lower(),
                auth_domain=qget("auth_domain").lower(), date_from=qget("date_from"), date_to=qget("date_to"),
                page=1, per_page=100000,
                include_internal=qget("include_internal") in ("1", "true", "yes"),
            )
            output = io.StringIO()
            cols = ["message_id", "timestamp", "auth_domain", "auth_username",
                    "identity", "sender", "recipient", "main_class", "subject",
                    "status", "relay", "reason", "size", "queue_id"]
            writer = csv.DictWriter(output, fieldnames=cols, extrasaction="ignore")
            writer.writeheader()
            for r in rows:
                writer.writerow(r)
            fname = f"zimbra_mail_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            self._send_bytes(
                output.getvalue().encode("utf-8"),
                "text/csv; charset=utf-8",
                extra_headers={"Content-Disposition": f'attachment; filename="{fname}"'}
            )
            return

        self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self._send_json({"error": "Request too large"}, 413)
            self.close_connection = True
            return
        if length > 65536 and path != "/api/upload":
            self._send_json({"error": "Request too large"}, 413)
            self.close_connection = True
            return
        body = self.rfile.read(length) if length > 0 else b""

        # Login
        if path == "/api/login":
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
            except Exception:
                data = {}
            user = data.get("username", "")
            pwd = data.get("password", "")
            expected_user = _runtime_cfg.get("username", "")
            expected_hash = _runtime_cfg.get("password_hash", "")

            if not expected_user or not expected_hash:
                token = _create_session()
                self._send_json({"success": True}, 200, extra_headers={"Set-Cookie": self._session_cookie(token)})
                return

            ip = self.client_address[0]
            if _login_blocked(ip):
                self._send_json({"success": False, "error": "TOO MANY FAILED ATTEMPTS, TRY LATER"}, 429)
                return
            if (isinstance(user, str) and isinstance(pwd, str)
                    and secrets.compare_digest(user, expected_user) and _verify_password(pwd, expected_hash)):
                _upgrade_password_hash(pwd)
                token = _create_session()
                self._send_json({"success": True}, 200, extra_headers={"Set-Cookie": self._session_cookie(token)})
            else:
                _login_failed(ip)
                print(f"[WARN] failed dashboard login from {ip}")
                self._send_json({"success": False, "error": "ACCESS DENIED: INVALID CREDENTIALS"}, 401)
            return

        # VPS diagnostic agent: authenticated with a dedicated root-only token.
        if path == "/api/doctor/report":
            supplied = self.headers.get("X-ZMT-Doctor-Token", "")
            if not _DOCTOR_TOKEN or not secrets.compare_digest(supplied, _DOCTOR_TOKEN):
                self._send_json({"error": "Invalid doctor token"}, 401)
                return
            if len(body) > 65536:
                self._send_json({"error": "Report too large"}, 413)
                return
            try:
                report = json.loads(body.decode("utf-8"))
                if not isinstance(report, dict) or not isinstance(report.get("checks"), list):
                    raise ValueError("Invalid report structure")
                report["checks"] = report["checks"][:40]
                report["hostname"] = str(report.get("hostname", ""))[:255]
                report["checked_at"] = str(report.get("checked_at", ""))[:64]
                report["overall"] = str(report.get("overall", "UNKNOWN"))[:16]
                for item in report["checks"]:
                    if isinstance(item, dict):
                        item["name"] = str(item.get("name", ""))[:100]
                        item["status"] = str(item.get("status", "UNKNOWN"))[:16]
                        item["detail"] = str(item.get("detail", ""))[:500]
                _save_doctor_report(report)
                self._send_json({"success": True, "checked_at": report["checked_at"]})
            except (ValueError, OSError) as exc:
                self._send_json({"error": f"Invalid report: {exc}"}, 400)
            return

        if not self._check_auth():
            self._send_json({"error": "Unauthorized"}, 401)
            return

        # Upload manual log
        if path == "/api/upload":
            content_type = self.headers.get("Content-Type", "")
            if "boundary=" in content_type:
                boundary = content_type.split("boundary=")[1].strip().encode()
                parts = body.split(b"--" + boundary)
                for part in parts:
                    if b"filename=" in part:
                        header_end = part.find(b"\r\n\r\n")
                        payload = part[header_end + 4:].rstrip(b"\r\n")
                        text = payload.decode("utf-8", errors="ignore")
                        records = parse_log_content(text)
                        count = db.insert_records(records)
                        self._send_json({"success": True, "count": count})
                        return
            self._send_json({"error": "Upload failed"}, 400)
            return

        self._send_json({"error": "Not found"}, 404)


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        # Handshake TLS gagal / klien putus: bukan error aplikasi.
        if isinstance(exc, (ssl.SSLError, ConnectionError, TimeoutError, OSError)):
            return
        super().handle_error(request, client_address)


def run_server():
    global _TLS_ENABLED
    host = _runtime_cfg.get("host", "0.0.0.0")
    port = int(_runtime_cfg.get("port", 9444))
    httpd = QuietThreadingHTTPServer((host, port), TrackerHandler)

    cert = _runtime_cfg.get("cert", "")
    key = _runtime_cfg.get("key", "")
    proto = "http"
    if cert and key and os.path.isfile(cert) and os.path.isfile(key):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        # Handshake dilakukan di thread handler (bukan di loop accept) agar klien
        # lambat tidak memblokir server.
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
        proto = "https"
        _TLS_ENABLED = True

    print(f"[*] {config.APP_NAME} running at {proto}://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()

if __name__ == "__main__":
    run_server()
