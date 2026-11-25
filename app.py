"""
Zimbra Mail Tracker - Flask Backend
"""

import os
import io
import csv
import json
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, send_file
from parser import parse_log_content, parse_log_directory, parse_log_file
import config

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB max upload

# In-memory store untuk data log yang sudah diparsing
_log_cache = []

# ─── Demo data generator ──────────────────────────────────────────────────────

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


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", config=config)


@app.route("/api/logs", methods=["GET"])
def get_logs():
    """Ambil data log dengan filter, sort, dan pagination."""
    global _log_cache

    data = _log_cache if _log_cache else DEMO_DATA

    # ── Filter ──────────────────────────────────────────────────────────────
    q = request.args.get("q", "").strip().lower()
    status_f = request.args.get("status", "").lower()
    main_class_f = request.args.get("main_class", "").lower()
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    domain_f = request.args.get("auth_domain", "").lower()

    filtered = []
    for row in data:
        if q and not any(
            q in str(v).lower() for v in row.values()
        ):
            continue
        if status_f and row.get("status", "") != status_f:
            continue
        if main_class_f and row.get("main_class", "") != main_class_f:
            continue
        if domain_f and domain_f not in row.get("auth_domain", "").lower():
            continue
        if date_from:
            if row.get("timestamp", "") < date_from:
                continue
        if date_to:
            if row.get("timestamp", "") > date_to + " 23:59:59":
                continue
        filtered.append(row)

    # ── Sort ────────────────────────────────────────────────────────────────
    sort_by = request.args.get("sort", "timestamp")
    sort_dir = request.args.get("dir", "desc")
    reverse = sort_dir == "desc"
    valid_cols = {"message_id", "timestamp", "auth_domain", "auth_username",
                  "identity", "from", "to", "main_class", "subject", "status", "size"}
    if sort_by in valid_cols:
        filtered.sort(key=lambda x: str(x.get(sort_by, "")), reverse=reverse)

    # ── Stats ───────────────────────────────────────────────────────────────
    total = len(filtered)
    stats = {
        "total": total,
        "delivered": sum(1 for r in filtered if r.get("status") == "delivered"),
        "sent": sum(1 for r in filtered if r.get("status") == "sent"),
        "bounced": sum(1 for r in filtered if r.get("status") == "bounced"),
        "deferred": sum(1 for r in filtered if r.get("status") == "deferred"),
        "spam": sum(1 for r in filtered if r.get("main_class") == "spam"),
    }

    # Daily chart data (last 7 days)
    chart = _build_chart(filtered)

    # ── Pagination ──────────────────────────────────────────────────────────
    page = max(1, int(request.args.get("page", 1)))
    per_page = int(request.args.get("per_page", 50))
    per_page = min(per_page, 500)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    start = (page - 1) * per_page
    end = start + per_page
    rows = filtered[start:end]

    return jsonify({
        "rows": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "stats": stats,
        "chart": chart,
        "is_demo": not bool(_log_cache),
    })


@app.route("/api/upload", methods=["POST"])
def upload_log():
    """Upload file log untuk diparsing."""
    global _log_cache
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    content = file.read().decode("utf-8", errors="ignore")
    try:
        records = parse_log_content(content)
        _log_cache = records
        return jsonify({"success": True, "count": len(records), "filename": file.filename})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/load-dir", methods=["POST"])
def load_dir():
    """Load log dari direktori server."""
    global _log_cache
    data = request.get_json()
    log_dir = data.get("path", config.LOG_DIR)
    if not os.path.isdir(log_dir):
        return jsonify({"error": f"Directory not found: {log_dir}"}), 404
    try:
        records = parse_log_directory(log_dir)
        _log_cache = records
        return jsonify({"success": True, "count": len(records), "path": log_dir})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/clear", methods=["POST"])
def clear_cache():
    global _log_cache
    _log_cache = []
    return jsonify({"success": True})


@app.route("/api/export/csv", methods=["GET"])
def export_csv():
    """Export data ke CSV."""
    global _log_cache
    data = _log_cache if _log_cache else DEMO_DATA
    output = io.StringIO()
    cols = ["message_id", "timestamp", "auth_domain", "auth_username",
            "identity", "from", "to", "main_class", "subject", "status",
            "relay", "reason", "size", "queue_id"]
    writer = csv.DictWriter(output, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for row in data:
        writer.writerow(row)
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"mail_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
    )


@app.route("/api/stats/summary", methods=["GET"])
def stats_summary():
    global _log_cache
    data = _log_cache if _log_cache else DEMO_DATA
    chart = _build_chart(data)
    return jsonify(chart)


def _build_chart(data):
    """Buat data chart per hari (7 hari terakhir)."""
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


if __name__ == "__main__":
    app.run(
        host=config.HOST,
        port=config.PORT,
        debug=config.DEBUG,
    )
