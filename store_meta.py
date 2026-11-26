"""
Read-only metadata extraction from Zimbra blob files (/opt/zimbra/store/**/*.msg).

Per Message-ID collects: Subject, To, Cc and attachment metadata (file name,
MIME type, decoded size). Attachment CONTENT and message bodies are never stored
or returned; only sizes are computed. Blobs are opened read-only; nothing in the
Zimbra store is modified.
"""

import os
import gzip
from email import policy
from email.parser import BytesParser
from email.header import decode_header, make_header
from email.utils import getaddresses

STORE = os.getenv("ZIMBRA_STORE", "/opt/zimbra/store")
MAX_PARSE_BYTES = 64 * 1024 * 1024   # blob lebih besar: hanya header yang dibaca


def _decode(value):
    if value is None:
        return ""
    try:
        s = str(make_header(decode_header(str(value))))
    except Exception:
        s = str(value)
    return " ".join(s.split())


def _addresses(values):
    out = []
    for name, addr in getaddresses([_decode(v) for v in values if v]):
        addr = (addr or "").strip()
        if not addr and not name:
            continue
        name = (name or "").strip().strip("'\"").strip()
        out.append(f"{name} <{addr}>" if name and addr and name != addr else (addr or name))
    return out


def _part_size(part):
    cte = (part.get("Content-Transfer-Encoding") or "").strip().lower()
    payload = part.get_payload(decode=False)
    if not isinstance(payload, str):
        return 0
    if cte == "base64":
        s = "".join(payload.split())
        return max(0, len(s) * 3 // 4 - (2 if s.endswith("==") else 1 if s.endswith("=") else 0))
    if cte == "quoted-printable":
        data = part.get_payload(decode=True)
        return len(data) if data else 0
    return len(payload.encode("utf-8", "surrogateescape"))


def _attachments(msg):
    atts = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = (part.get("Content-Disposition") or "").strip().lower()
        fname = part.get_filename()
        ctype = part.get_content_type()
        is_att = disp.startswith("attachment") or bool(fname)
        if not is_att and disp.startswith("inline") and not ctype.startswith("text/"):
            is_att = True
        if not is_att:
            continue
        atts.append({
            "name": _decode(fname) if fname else "(tanpa nama)",
            "type": ctype,
            "size": _part_size(part),
            "inline": disp.startswith("inline"),
        })
    return atts


def _open(path):
    with open(path, "rb") as f:
        magic = f.read(2)
    return gzip.open(path, "rb") if magic == b"\x1f\x8b" else open(path, "rb")


def parse_blob(path, with_attachments=True):
    """Return dict or None (no Message-ID)."""
    size_on_disk = os.path.getsize(path)
    with _open(path) as f:
        if with_attachments and size_on_disk <= MAX_PARSE_BYTES:
            msg = BytesParser(policy=policy.compat32).parse(f)
            atts = _attachments(msg)
        else:
            msg = BytesParser(policy=policy.compat32).parse(f, headersonly=True)
            atts = None
    mid = (msg.get("Message-ID") or msg.get("Message-Id") or "").strip().strip("<>").strip()
    if not mid:
        return None
    return {
        "message_id": mid,
        "subject": _decode(msg.get("Subject")),
        "has_subject": msg.get("Subject") is not None,
        "to": _addresses(msg.get_all("To", [])),
        "cc": _addresses(msg.get_all("Cc", [])),
        "attachments": atts,
    }


def scan(since_epoch, store=STORE, with_attachments=True):
    """Scan blobs with mtime >= since_epoch. Returns (meta_by_msgid, stats)."""
    meta, subjects = {}, {}
    stats = {"files": 0, "errors": 0, "no_message_id": 0, "newest_mtime": since_epoch}
    for root, _dirs, names in os.walk(store):
        for n in names:
            if not n.endswith(".msg"):
                continue
            p = os.path.join(root, n)
            try:
                mt = os.path.getmtime(p)
                if mt < since_epoch:
                    continue
                stats["files"] += 1
                stats["newest_mtime"] = max(stats["newest_mtime"], mt)
                d = parse_blob(p, with_attachments)
                if d is None:
                    stats["no_message_id"] += 1
                    continue
                mid = d["message_id"]
                subjects.setdefault(mid, set()).add(d["subject"] if d["has_subject"] else "")
                cur = meta.get(mid)
                if cur is None:
                    meta[mid] = {k: d[k] for k in ("to", "cc", "attachments")}
                else:
                    for k in ("to", "cc"):
                        if not cur[k] and d[k]:
                            cur[k] = d[k]
                    if cur["attachments"] is None and d["attachments"] is not None:
                        cur["attachments"] = d["attachments"]
            except Exception:
                stats["errors"] += 1
    for mid, ss in subjects.items():
        # Subject hanya dipakai bila semua salinan blob sepakat.
        meta[mid]["subject"] = next(iter(ss)) if len(ss) == 1 else None
    return meta, stats
