"""
Zimbra Mail Log Parser
Streaming parser yang membaca SEMUA baris log tanpa beban memory.
Mendukung plain text log dan gzip (.gz) log rotasi, format syslog klasik
("Nov 26 00:01:10 host ...") maupun ISO8601 ("2026-11-26T00:01:10.123+07:00 host ...").

Catatan offset: file plain dibaca dalam mode biner sehingga offset yang
di-yield adalah offset BYTE yang valid untuk f.seek(). Baris terakhir yang
belum lengkap (belum diakhiri newline) tidak dikonsumsi agar tidak terpotong.
"""

import re
import os
import gzip
from datetime import datetime, timedelta
from collections import defaultdict

_TS = (
    r"(?:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
    r"|(?P<month>\w+)\s+(?P<day>\d+)\s+(?P<time>[\d:]+))\s+\S+\s+"
)

# smtpd bisa muncul sebagai postfix/smtpd, postfix/smtps/smtpd, postfix/submission/smtpd
RE_AUTH = re.compile(
    _TS + r"postfix/(?:[\w.-]+/)?smtpd\[\d+\]:\s+(?P<queue_id>\w+): client="
)
RE_SASL_USER = re.compile(r"sasl_username=(?P<auth_user>[^\s,]+)")

RE_MSGID = re.compile(
    _TS + r"postfix/(?:[\w.-]+/)?cleanup\[\d+\]:\s+(?P<queue_id>\w+): message-id=<?(?P<message_id>[^>\s]*)>?"
)

RE_FROM = re.compile(
    _TS + r"postfix/(?:[\w.-]+/)?(?:qmgr|pickup)\[\d+\]:\s+(?P<queue_id>\w+): "
    r"from=<(?P<from>[^>]*)>,\s*(?:uid=\d+\s+)?size=(?P<size>\d+)"
)

RE_SENT = re.compile(
    _TS + r"postfix/(?:[\w.-]+/)?smtp\[\d+\]:\s+(?P<queue_id>\w+): "
    r"to=<(?P<to>[^>]+)>, (?:orig_to=<[^>]*>, )?relay=(?P<relay>[^,]+),.*?status=sent"
)

RE_DELIVERED = re.compile(
    _TS + r"postfix/(?:[\w.-]+/)?lmtp\[\d+\]:\s+(?P<queue_id>\w+): "
    r"to=<(?P<to>[^>]+)>, (?:orig_to=<[^>]*>, )?relay=(?P<relay>[^,]+),.*?status=sent"
)

RE_BOUNCED = re.compile(
    _TS + r"postfix/\S+\[\d+\]:\s+(?P<queue_id>\w+): "
    r"to=<(?P<to>[^>]+)>,.*?status=bounced.*?\((?P<reason>[^)]+)\)"
)

RE_DEFERRED = re.compile(
    _TS + r"postfix/\S+\[\d+\]:\s+(?P<queue_id>\w+): "
    r"to=<(?P<to>[^>]+)>,.*?status=deferred.*?\((?P<reason>[^)]+)\)"
)

# Amavis menulis Subject dengan tanda kutip; kutip di dalam subject di-escape.
RE_SUBJECT_AMAVIS = re.compile(r'Subject:\s*"(?P<subject>(?:[^"\\]|\\.)*)"')
RE_AMAVIS_QUEUE_ID = re.compile(r"Queue-ID:\s*(?P<queue_id>[A-Za-z0-9]+)")
RE_AMAVIS_QUEUED_AS = re.compile(r"queued_as:\s*(?P<queue_id>[A-Za-z0-9]+)")
RE_AMAVIS_MESSAGE_ID = re.compile(r"Message-ID:\s*<(?P<message_id>[^>]+)>")
# Verdict resmi amavis: "Passed CLEAN", "Passed SPAMMY", "Blocked SPAM", ...
RE_AMAVIS_VERDICT = re.compile(r"amavis\[\d+\]:\s+\([\w-]+\)\s+(?:Passed|Blocked)\s+(?P<verdict>[A-Z][A-Z-]*)")
SPAM_VERDICTS = {"SPAM", "SPAMMY"}

MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

_RE_ISO = re.compile(r"^(?P<iso>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)")


def _parse_ts(month_str=None, day_str=None, time_str=None, year=None, line=None):
    """Parse ISO 8601 and traditional syslog timestamps (local time, naive)."""
    if line:
        match = _RE_ISO.match(line)
        if match:
            value = match.group("iso")
            try:
                # Waktu lokal yang tertulis di log dipertahankan (konsisten dengan format syslog).
                return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                pass

    if month_str and day_str and time_str:
        try:
            month = MONTH_MAP.get(month_str[:3].title(), 0)
            if not month:
                return None
            day = int(day_str)
            h, m, sec = map(int, time_str.split(":")[:3])
            now = datetime.now()
            ts = datetime(year or now.year, month, day, h, m, sec)
            # Syslog tidak menyimpan tahun: log Desember yang dibaca bulan Januari
            # akan terlihat "di masa depan" -> mundurkan satu tahun.
            if year is None and ts > now + timedelta(days=1):
                ts = ts.replace(year=ts.year - 1)
            return ts
        except Exception:
            pass
    return None


def _extract_domain(email: str) -> str:
    if "@" in email:
        return email.split("@", 1)[1]
    return ""


def _new_queue():
    return {
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
        "verdict": "",
        "recipients": [],
    }


def _unescape_subject(s: str) -> str:
    return s.replace('\\"', '"').replace("\\\\", "\\").strip()


def stream_parse_log(filepath: str, start_offset: int = 0, batch_size: int = 1000, meta_sink=None):
    """
    Generator streaming log parser.
    Membaca baris demi baris dari start_offset (byte) dan yield (batch_records, new_offset).

    meta_sink (opsional, dict): diisi dengan metadata per queue dan subject per
    Message-ID yang terlihat di file ini, termasuk queue yang belum punya baris
    delivery. Pemanggil menyimpannya (db.upsert_queue_meta) sehingga korelasi
    tidak hilang ketika delivery muncul pada sinkronisasi berikutnya.
    """
    is_gz = filepath.endswith(".gz")
    if is_gz:
        f = gzip.open(filepath, "rb")
        pos = 0
    else:
        f = open(filepath, "rb")
        if start_offset > 0:
            f.seek(start_offset)
        pos = start_offset

    queue_data = defaultdict(_new_queue)
    qids_by_msgid = defaultdict(set)
    subject_by_msgid = {}
    pending = 0

    def set_subject(qid, subject):
        if qid and subject:
            queue_data[qid]["queue_id"] = qid
            queue_data[qid]["subject"] = subject

    try:
        while True:
            raw = f.readline()
            if not raw:
                break
            if not is_gz and not raw.endswith(b"\n"):
                # Baris belum selesai ditulis: jangan konsumsi, baca ulang di sinkronisasi berikutnya.
                break
            pos += len(raw)
            if b"postfix/" not in raw and b"amavis[" not in raw:
                continue
            line = raw.decode("utf-8", errors="replace")

            if "message-id=" in line and "cleanup[" in line:
                m = RE_MSGID.search(line)
                if m:
                    qid = m.group("queue_id")
                    msgid = m.group("message_id").strip("<>")
                    queue_data[qid]["queue_id"] = qid
                    if msgid:
                        queue_data[qid]["message_id"] = msgid
                        qids_by_msgid[msgid].add(qid)
                        if msgid in subject_by_msgid and not queue_data[qid]["subject"]:
                            queue_data[qid]["subject"] = subject_by_msgid[msgid]
                    continue

            if "sasl_username=" in line and "smtpd[" in line:
                m = RE_AUTH.search(line)
                um = RE_SASL_USER.search(line) if m else None
                if m and um:
                    qid = m.group("queue_id")
                    auth_user = um.group("auth_user")
                    queue_data[qid]["queue_id"] = qid
                    queue_data[qid]["auth_username"] = auth_user
                    queue_data[qid]["auth_domain"] = _extract_domain(auth_user)
                    if not queue_data[qid]["timestamp"]:
                        queue_data[qid]["timestamp"] = _parse_ts(m.group("month"), m.group("day"), m.group("time"), line=line)
                    continue

            if "from=<" in line and ("qmgr[" in line or "pickup[" in line):
                m = RE_FROM.search(line)
                if m:
                    qid = m.group("queue_id")
                    sender = m.group("from")
                    q = queue_data[qid]
                    q["queue_id"] = qid
                    q["from"] = sender
                    q["size"] = int(m.group("size"))
                    if not q["identity"]:
                        q["identity"] = sender
                    if not q["timestamp"]:
                        q["timestamp"] = _parse_ts(m.group("month"), m.group("day"), m.group("time"), line=line)
                    continue

            if "status=sent" in line:
                if "smtp[" in line and "lmtp[" not in line:
                    m = RE_SENT.search(line)
                    if m:
                        queue_data[m.group("queue_id")]["recipients"].append({
                            "to": m.group("to"), "status": "sent",
                            "relay": m.group("relay"), "reason": "",
                        })
                        pending += 1
                elif "lmtp[" in line:
                    m = RE_DELIVERED.search(line)
                    if m:
                        queue_data[m.group("queue_id")]["recipients"].append({
                            "to": m.group("to"), "status": "delivered",
                            "relay": m.group("relay"), "reason": "",
                        })
                        pending += 1

            elif "status=bounced" in line:
                m = RE_BOUNCED.search(line)
                if m:
                    queue_data[m.group("queue_id")]["recipients"].append({
                        "to": m.group("to"), "status": "bounced",
                        "relay": "", "reason": m.group("reason"),
                    })
                    pending += 1

            elif "status=deferred" in line:
                m = RE_DEFERRED.search(line)
                if m:
                    queue_data[m.group("queue_id")]["recipients"].append({
                        "to": m.group("to"), "status": "deferred",
                        "relay": "", "reason": m.group("reason"),
                    })
                    pending += 1

            elif "amavis[" in line and ("Queue-ID:" in line or "queued_as:" in line):
                q1 = RE_AMAVIS_QUEUE_ID.search(line)
                q2 = RE_AMAVIS_QUEUED_AS.search(line)
                qids = [x.group("queue_id") for x in (q1, q2) if x]
                mm = RE_AMAVIS_MESSAGE_ID.search(line)
                msgid = mm.group("message_id").strip("<>") if mm else ""

                sm = RE_SUBJECT_AMAVIS.search(line) if "Subject:" in line else None
                if sm:
                    subject = _unescape_subject(sm.group("subject"))
                    if subject:
                        if msgid:
                            subject_by_msgid[msgid] = subject
                            for other in qids_by_msgid.get(msgid, ()):
                                set_subject(other, subject)
                        for qid in qids:
                            set_subject(qid, subject)

                vm = RE_AMAVIS_VERDICT.search(line)
                if vm:
                    verdict = vm.group("verdict")
                    cls = "spam" if verdict in SPAM_VERDICTS else "not-spam"
                    for qid in qids:
                        queue_data[qid]["queue_id"] = qid
                        queue_data[qid]["verdict"] = verdict
                        queue_data[qid]["main_class"] = cls

                if msgid:
                    for qid in qids:
                        qids_by_msgid[msgid].add(qid)
                        if not queue_data[qid]["message_id"]:
                            queue_data[qid]["message_id"] = msgid

            if pending >= batch_size:
                yield _flush_queue(queue_data, qids_by_msgid), pos
                pending = 0

        flushed = _flush_queue(queue_data, qids_by_msgid)
        if meta_sink is not None:
            _export_meta(meta_sink, queue_data, subject_by_msgid)
        if flushed or meta_sink is not None:
            yield flushed, pos

    finally:
        f.close()


def _sibling_auth(queue_dict, qids_by_msgid, data):
    """Ambil SASL user dari queue lain dengan Message-ID sama (hop sebelum amavis)."""
    msgid = data.get("message_id")
    if not msgid:
        return "", ""
    users = {queue_dict[q]["auth_username"] for q in qids_by_msgid.get(msgid, ())
             if q in queue_dict and queue_dict[q]["auth_username"]}
    if len(users) == 1:
        u = users.pop()
        return u, _extract_domain(u)
    return "", ""


def _flush_queue(queue_dict, qids_by_msgid=None):
    """Ubah recipient yang terkumpul menjadi record. Metadata queue tetap disimpan
    agar baris delivery berikutnya untuk queue yang sama tetap terkorelasi."""
    results = []
    qids_by_msgid = qids_by_msgid or {}
    for qid, data in list(queue_dict.items()):
        recipients = data.get("recipients", [])
        if not recipients:
            continue

        sender = data["from"]
        auth_user, auth_domain = data["auth_username"], data["auth_domain"]
        if not auth_user:
            auth_user, auth_domain = _sibling_auth(queue_dict, qids_by_msgid, data)
        auth_user = auth_user or sender
        auth_domain = auth_domain or _extract_domain(sender)
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
        data["recipients"] = []
    return results


def _export_meta(sink, queue_dict, subject_by_msgid):
    queues = sink.setdefault("queues", {})
    for qid, d in queue_dict.items():
        if not qid:
            continue
        ts = d["timestamp"]
        new = {
            "message_id": d["message_id"],
            "sender": d["from"],
            "size": d["size"] or 0,
            "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "",
            "auth_username": d["auth_username"],
            "auth_domain": d["auth_domain"],
            "subject": d["subject"],
            "verdict": d["verdict"],
        }
        old = queues.get(qid)
        if old:
            for k, v in new.items():
                if v and not old.get(k):
                    old[k] = v
        else:
            queues[qid] = new
    subj = sink.setdefault("subjects", {})
    for msgid, s in subject_by_msgid.items():
        if s:
            subj[msgid] = s


def parse_log_content(content: str) -> list:
    """Parse string upload."""
    import tempfile
    if not content.endswith("\n"):
        content += "\n"
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
