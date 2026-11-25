"""
Zimbra Mail Log Parser
Parsing log file Zimbra (maillog/mail.log) dengan field lengkap:
Message ID, Timestamp, Auth Domain, Auth Username, Identity,
Sender, Recipient, Main Class (spam), Subject, Status
"""

import re
import os
import gzip
from datetime import datetime
from collections import defaultdict

# ─── Regex Patterns ───────────────────────────────────────────────────────────

# postfix/smtpd: client auth (SASL) → auth_username & auth_domain
RE_AUTH = re.compile(
    r"(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+)\s+\S+\s+"
    r"postfix/smtpd\[\d+\]:\s+(?P<queue_id>\w+): client=\S+"
    r"(?:,\s*sasl_username=(?P<auth_user>\S+))?"
)

# postfix/cleanup: message-id header
RE_MSGID = re.compile(
    r"(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+)\s+\S+\s+"
    r"postfix/cleanup\[\d+\]:\s+(?P<queue_id>\w+): message-id=<(?P<message_id>[^>]*)>"
)

# postfix/qmgr or pickup: from + size
RE_FROM = re.compile(
    r"(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+)\s+\S+\s+"
    r"postfix/(?:qmgr|pickup)\[\d+\]:\s+(?P<queue_id>\w+): "
    r"from=<(?P<from>[^>]*)>,\s*size=(?P<size>\d+)"
)

# postfix/smtp (external) → sent
RE_SENT = re.compile(
    r"(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+)\s+\S+\s+"
    r"postfix/smtp\[\d+\]:\s+(?P<queue_id>\w+): "
    r"to=<(?P<to>[^>]+)>, relay=(?P<relay>[^,]+),.*?status=sent"
)

# postfix/lmtp (internal delivery) → delivered
RE_DELIVERED = re.compile(
    r"(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+)\s+\S+\s+"
    r"postfix/lmtp\[\d+\]:\s+(?P<queue_id>\w+): "
    r"to=<(?P<to>[^>]+)>, relay=(?P<relay>[^,]+),.*?status=sent"
)

# bounced
RE_BOUNCED = re.compile(
    r"(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+)\s+\S+\s+"
    r"postfix/\S+\[\d+\]:\s+(?P<queue_id>\w+): "
    r"to=<(?P<to>[^>]+)>,.*?status=bounced.*?\((?P<reason>[^)]+)\)"
)

# deferred
RE_DEFERRED = re.compile(
    r"(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+)\s+\S+\s+"
    r"postfix/\S+\[\d+\]:\s+(?P<queue_id>\w+): "
    r"to=<(?P<to>[^>]+)>,.*?status=deferred.*?\((?P<reason>[^)]+)\)"
)

# amavis spam classification
RE_SPAM = re.compile(
    r"(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+)\s+\S+\s+"
    r"amavis\[\d+\]:\s+.*?(?P<queue_id>\w{10,}): "
    r".*?Hits:\s*(?P<hits>[\d.\-]+).*?tag2=(?P<tag2>[\d.\-]+).*?kill=(?P<kill>[\d.\-]+)"
)

# amavis subject line
RE_SUBJECT_AMAVIS = re.compile(
    r"(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+)\s+\S+\s+"
    r"amavis\[\d+\]:\s+.*?Subject:\s*(?P<subject>.+)"
)

# MONTH map
MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_ts(month_str, day_str, time_str, year=None):
    if year is None:
        year = datetime.now().year
    try:
        month = MONTH_MAP.get(month_str, 1)
        day = int(day_str)
        h, m, s = map(int, time_str.split(":"))
        return datetime(year, month, day, h, m, s)
    except Exception:
        return None


def _extract_domain(email: str) -> str:
    """Ambil domain dari email address."""
    if "@" in email:
        return email.split("@", 1)[1]
    return ""


def parse_log_file(filepath: str) -> list:
    """
    Parse satu file log Zimbra. Mengembalikan list of dict per email.
    """
    queue_data: dict = defaultdict(lambda: {
        "message_id": "",
        "queue_id": "",
        "timestamp": None,
        "auth_username": "",
        "auth_domain": "",
        "identity": "",
        "from": "",
        "size": 0,
        "subject": "",
        "main_class": "not-spam",
        "spam_hits": None,
        "recipients": [],
    })

    def open_log(path):
        if path.endswith(".gz"):
            return gzip.open(path, "rt", encoding="utf-8", errors="ignore")
        return open(path, "r", encoding="utf-8", errors="ignore")

    with open_log(filepath) as f:
        for line in f:
            if "postfix/" not in line and "amavis[" not in line:
                continue
            line = line.rstrip()

            # Message-ID
            m = RE_MSGID.search(line)
            if m:
                qid = m.group("queue_id")
                queue_data[qid]["queue_id"] = qid
                queue_data[qid]["message_id"] = m.group("message_id")
                continue

            # Auth (SASL)
            m = RE_AUTH.search(line)
            if m and m.group("auth_user"):
                qid = m.group("queue_id")
                auth_user = m.group("auth_user")
                queue_data[qid]["auth_username"] = auth_user
                queue_data[qid]["auth_domain"] = _extract_domain(auth_user)
                if not queue_data[qid]["timestamp"]:
                    ts = _parse_ts(m.group("month"), m.group("day"), m.group("time"))
                    queue_data[qid]["timestamp"] = ts
                continue

            # FROM + size
            m = RE_FROM.search(line)
            if m:
                qid = m.group("queue_id")
                sender = m.group("from")
                queue_data[qid]["queue_id"] = qid
                queue_data[qid]["from"] = sender
                queue_data[qid]["size"] = int(m.group("size"))
                # identity = sender kalau belum ada auth_username
                if not queue_data[qid]["identity"]:
                    queue_data[qid]["identity"] = sender
                if not queue_data[qid]["timestamp"]:
                    ts = _parse_ts(m.group("month"), m.group("day"), m.group("time"))
                    queue_data[qid]["timestamp"] = ts
                # auth_domain dari sender kalau belum ada
                if not queue_data[qid]["auth_domain"]:
                    queue_data[qid]["auth_domain"] = _extract_domain(sender)
                continue

            # SENT (external)
            m = RE_SENT.search(line)
            if m:
                qid = m.group("queue_id")
                queue_data[qid]["recipients"].append({
                    "to": m.group("to"),
                    "status": "sent",
                    "relay": m.group("relay"),
                    "reason": "",
                })
                continue

            # DELIVERED (lmtp/internal)
            m = RE_DELIVERED.search(line)
            if m:
                qid = m.group("queue_id")
                queue_data[qid]["recipients"].append({
                    "to": m.group("to"),
                    "status": "delivered",
                    "relay": m.group("relay"),
                    "reason": "",
                })
                continue

            # BOUNCED
            m = RE_BOUNCED.search(line)
            if m:
                qid = m.group("queue_id")
                queue_data[qid]["recipients"].append({
                    "to": m.group("to"),
                    "status": "bounced",
                    "relay": "",
                    "reason": m.group("reason"),
                })
                continue

            # DEFERRED
            m = RE_DEFERRED.search(line)
            if m:
                qid = m.group("queue_id")
                queue_data[qid]["recipients"].append({
                    "to": m.group("to"),
                    "status": "deferred",
                    "relay": "",
                    "reason": m.group("reason"),
                })
                continue

            # SPAM score (amavis)
            m = RE_SPAM.search(line)
            if m:
                qid = m.group("queue_id")
                try:
                    hits = float(m.group("hits"))
                    tag2 = float(m.group("tag2"))
                    queue_data[qid]["spam_hits"] = hits
                    queue_data[qid]["main_class"] = "spam" if hits >= tag2 else "not-spam"
                except Exception:
                    pass
                continue

            # Subject (amavis)
            m = RE_SUBJECT_AMAVIS.search(line)
            if m:
                subj = m.group("subject").strip()
                # cari queue_id terkait dari baris yang sama (tidak ada di regex ini)
                # simpan sementara
                queue_data["__subj__"]["subject"] = subj
                continue

    # Build flat result list
    results = []
    for qid, data in queue_data.items():
        if qid == "__subj__":
            continue

        sender = data["from"]
        auth_user = data["auth_username"] or sender
        auth_domain = data["auth_domain"] or _extract_domain(sender)
        identity = auth_user or sender
        ts = data["timestamp"]
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if ts else ""

        base = {
            "message_id": data["message_id"] or qid,
            "queue_id": qid,
            "timestamp": ts_str,
            "auth_domain": auth_domain,
            "auth_username": auth_user,
            "identity": identity,
            "from": sender,
            "size": data["size"],
            "subject": data["subject"],
            "main_class": data["main_class"],
        }

        recipients = data["recipients"]
        if not recipients:
            results.append({**base, "to": "", "status": "unknown", "relay": "", "reason": ""})
        else:
            for rec in recipients:
                results.append({
                    **base,
                    "to": rec["to"],
                    "status": rec["status"],
                    "relay": rec.get("relay", ""),
                    "reason": rec.get("reason", ""),
                })

    # Sort by timestamp desc
    results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return results


def parse_log_directory(log_dir: str, max_files: int = 2) -> list:
    """Parse semua maillog* di direktori (termasuk .gz)."""
    all_results = []
    files = []
    for fname in os.listdir(log_dir):
        if re.match(r"(?:zimbra\.log|mail(?:log|\.log)).*", fname, re.IGNORECASE):
            files.append(os.path.join(log_dir, fname))

    files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    for fpath in files[:max_files]:
        try:
            all_results.extend(parse_log_file(fpath))
        except Exception as e:
            print(f"[WARN] Gagal parse {fpath}: {e}")

    all_results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return all_results


def parse_log_content(content: str) -> list:
    """Parse log dari string langsung (upload via browser)."""
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        return parse_log_file(tmp_path)
    finally:
        os.unlink(tmp_path)
