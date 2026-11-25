"""
Zimbra Mail Log Parser
Streaming parser yang membaca SEMUA baris log tanpa beban memory.
Mendukung plain text log dan gzip (.gz) log rotasi.
"""

import re
import os
import gzip
from datetime import datetime
from collections import defaultdict

RE_AUTH = re.compile(
    r"(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+)\s+\S+\s+"
    r"postfix/smtpd\[\d+\]:\s+(?P<queue_id>\w+): client=\S+"
    r"(?:,\s*sasl_username=(?P<auth_user>\S+))?"
)

RE_MSGID = re.compile(
    r"(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+)\s+\S+\s+"
    r"postfix/cleanup\[\d+\]:\s+(?P<queue_id>\w+): message-id=<(?P<message_id>[^>]*)>"
)

RE_FROM = re.compile(
    r"(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+)\s+\S+\s+"
    r"postfix/(?:qmgr|pickup)\[\d+\]:\s+(?P<queue_id>\w+): "
    r"from=<(?P<from>[^>]*)>,\s*size=(?P<size>\d+)"
)

RE_SENT = re.compile(
    r"(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+)\s+\S+\s+"
    r"postfix/smtp\[\d+\]:\s+(?P<queue_id>\w+): "
    r"to=<(?P<to>[^>]+)>, relay=(?P<relay>[^,]+),.*?status=sent"
)

RE_DELIVERED = re.compile(
    r"(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+)\s+\S+\s+"
    r"postfix/lmtp\[\d+\]:\s+(?P<queue_id>\w+): "
    r"to=<(?P<to>[^>]+)>, relay=(?P<relay>[^,]+),.*?status=sent"
)

RE_BOUNCED = re.compile(
    r"(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+)\s+\S+\s+"
    r"postfix/\S+\[\d+\]:\s+(?P<queue_id>\w+): "
    r"to=<(?P<to>[^>]+)>,.*?status=bounced.*?\((?P<reason>[^)]+)\)"
)

RE_DEFERRED = re.compile(
    r"(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+)\s+\S+\s+"
    r"postfix/\S+\[\d+\]:\s+(?P<queue_id>\w+): "
    r"to=<(?P<to>[^>]+)>,.*?status=deferred.*?\((?P<reason>[^)]+)\)"
)

RE_SPAM = re.compile(
    r"(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+)\s+\S+\s+"
    r"amavis\[\d+\]:\s+.*?(?P<queue_id>\w{10,}): "
    r".*?Hits:\s*(?P<hits>[\d.\-]+).*?tag2=(?P<tag2>[\d.\-]+).*?kill=(?P<kill>[\d.\-]+)"
)

RE_SUBJECT_AMAVIS = re.compile(
    r"(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+)\s+\S+\s+"
    r"amavis\[\d+\]:\s+.*?Subject:\s*(?P<subject>.+)"
)

RE_AMAVIS_QUEUE_ID = re.compile(r"(?:queued_as|Queue-ID):\s*(?P<queue_id>[A-Za-z0-9]+)")
RE_AMAVIS_MESSAGE_ID = re.compile(r"Message-ID:\s*<(?P<message_id>[^>]+)>")

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
    if "@" in email:
        return email.split("@", 1)[1]
    return ""

def stream_parse_log(filepath: str, start_offset: int = 0, batch_size: int = 1000):
    """
    Generator streaming log parser.
    Membaca baris demi baris dari start_offset dan yield batch hasil setiap batch_size.
    Mengembalikan (batch_records, new_offset).
    """
    is_gz = filepath.endswith(".gz")
    if is_gz:
        f = gzip.open(filepath, "rt", encoding="utf-8", errors="ignore")
    else:
        f = open(filepath, "r", encoding="utf-8", errors="ignore")
        if start_offset > 0:
            f.seek(start_offset)

    queue_data = defaultdict(lambda: {
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
        "recipients": [],
    })

    records_batch = []
    line_count = 0

    try:
        for line in f:
            line_count += 1
            if "postfix/" not in line and "amavis[" not in line:
                continue

            m = RE_MSGID.search(line)
            if m:
                qid = m.group("queue_id")
                queue_data[qid]["queue_id"] = qid
                queue_data[qid]["message_id"] = m.group("message_id")
                continue

            m = RE_AUTH.search(line)
            if m and m.group("auth_user"):
                qid = m.group("queue_id")
                auth_user = m.group("auth_user")
                queue_data[qid]["auth_username"] = auth_user
                queue_data[qid]["auth_domain"] = _extract_domain(auth_user)
                if not queue_data[qid]["timestamp"]:
                    queue_data[qid]["timestamp"] = _parse_ts(m.group("month"), m.group("day"), m.group("time"))
                continue

            m = RE_FROM.search(line)
            if m:
                qid = m.group("queue_id")
                sender = m.group("from")
                queue_data[qid]["queue_id"] = qid
                queue_data[qid]["from"] = sender
                queue_data[qid]["size"] = int(m.group("size"))
                if not queue_data[qid]["identity"]:
                    queue_data[qid]["identity"] = sender
                if not queue_data[qid]["timestamp"]:
                    queue_data[qid]["timestamp"] = _parse_ts(m.group("month"), m.group("day"), m.group("time"))
                if not queue_data[qid]["auth_domain"]:
                    queue_data[qid]["auth_domain"] = _extract_domain(sender)
                continue

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

            # Capture Subject before the spam rule because Amavis lines often
            # contain both "Hits:" and "Subject:" on the same log line.
            m = RE_SUBJECT_AMAVIS.search(line)
            if m:
                subject = m.group("subject").strip()
                qmatch = RE_AMAVIS_QUEUE_ID.search(line)
                qid = qmatch.group("queue_id") if qmatch else ""
                if qid and qid in queue_data:
                    queue_data[qid]["subject"] = subject
                else:
                    mmatch = RE_AMAVIS_MESSAGE_ID.search(line)
                    if mmatch:
                        msgid = mmatch.group("message_id").strip()
                        for item in queue_data.values():
                            if item.get("message_id", "").strip("<>") == msgid:
                                item["subject"] = subject

            m = RE_SPAM.search(line)
            if m:
                qid = m.group("queue_id")
                try:
                    hits = float(m.group("hits"))
                    tag2 = float(m.group("tag2"))
                    queue_data[qid]["main_class"] = "spam" if hits >= tag2 else "not-spam"
                except Exception:
                    pass
                continue

            # Check if queue has enough completed records to flush
            if len(queue_data) >= batch_size:
                flushed = _flush_queue(queue_data)
                new_pos = 0 if is_gz else f.tell()
                yield flushed, new_pos

        # Final flush
        flushed = _flush_queue(queue_data)
        new_pos = 0 if is_gz else f.tell()
        if flushed:
            yield flushed, new_pos

    finally:
        f.close()

def _flush_queue(queue_dict):
    results = []
    to_delete = []
    for qid, data in list(queue_dict.items()):
        if qid == "__subj__":
            continue
        recipients = data.get("recipients", [])
        if not recipients:
            continue

        sender = data["from"]
        auth_user = data["auth_username"] or sender
        auth_domain = data["auth_domain"] or _extract_domain(sender)
        identity = auth_user or sender
        ts = data["timestamp"]
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if ts else ""

        for rec in recipients:
            results.append({
                "message_id": data["message_id"] or qid,
                "queue_id": qid,
                "timestamp": ts_str,
                "auth_domain": auth_domain,
                "auth_username": auth_user,
                "identity": identity,
                "from": sender,
                "to": rec["to"],
                "status": rec["status"],
                "relay": rec.get("relay", ""),
                "reason": rec.get("reason", ""),
                "size": data["size"],
                "subject": data["subject"],
                "main_class": data["main_class"],
            })
        to_delete.append(qid)

    for qid in to_delete:
        del queue_dict[qid]
    return results

def parse_log_content(content: str) -> list:
    """Parse string upload."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False, encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    results = []
    try:
        for batch, _ in stream_parse_log(tmp_path):
            results.extend(batch)
        return results
    finally:
        os.unlink(tmp_path)
