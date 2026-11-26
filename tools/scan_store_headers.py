#!/usr/bin/env python3
"""
Read-only scan of Zimbra blob headers -> {Message-ID: Subject}.

Only the RFC822 header block of each *.msg blob is read (stops at the first
empty line, max 256 KB); message bodies are never read or stored. Zimbra
services and the blob store are not modified.

Usage: python3 tools/scan_store_headers.py --since 2026-10-24 --out /secure/dir/store_subjects.json
"""
import os
import sys
import gzip
import json
import argparse
from datetime import datetime
from email.parser import BytesHeaderParser
from email.header import decode_header, make_header
from email import policy

STORE = os.getenv("ZIMBRA_STORE", "/opt/zimbra/store")
MAX_HDR = 256 * 1024


def read_header_block(path):
    with open(path, "rb") as f:
        magic = f.read(2)
    opener = gzip.open if magic == b"\x1f\x8b" else open
    buf = bytearray()
    with opener(path, "rb") as f:
        for line in f:
            if line in (b"\r\n", b"\n"):
                break
            buf += line
            if len(buf) > MAX_HDR:
                break
    return bytes(buf)


def decode_subject(raw):
    if raw is None:
        return None
    try:
        s = str(make_header(decode_header(str(raw))))
    except Exception:
        s = str(raw)
    return " ".join(s.split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="YYYY-MM-DD; blobs with mtime before this are skipped")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    since = datetime.strptime(a.since, "%Y-%m-%d").timestamp()

    hp = BytesHeaderParser(policy=policy.compat32)
    seen = {}
    files = errors = no_msgid = 0
    for root, _dirs, names in os.walk(STORE):
        for n in names:
            if not n.endswith(".msg"):
                continue
            p = os.path.join(root, n)
            try:
                if os.path.getmtime(p) < since:
                    continue
                files += 1
                h = hp.parsebytes(read_header_block(p))
                mid = (h.get("Message-ID") or h.get("Message-Id") or "").strip()
                mid = mid.strip().lstrip("<").rstrip(">").strip()
                if not mid:
                    no_msgid += 1
                    continue
                subj = decode_subject(h.get("Subject"))
                seen.setdefault(mid, set()).add(subj if subj is not None else "")
            except Exception:
                errors += 1

    consistent = {m: next(iter(s)) for m, s in seen.items() if len(s) == 1}
    conflicting = {m: sorted(s) for m, s in seen.items() if len(s) > 1}
    out = {"generated_at": datetime.now().isoformat(timespec="seconds"), "since": a.since,
           "files_scanned": files, "errors": errors, "without_message_id": no_msgid,
           "message_ids": len(seen), "consistent": consistent, "conflicting": conflicting}
    fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(json.dumps({k: v for k, v in out.items() if k not in ("consistent", "conflicting")}
                     | {"consistent": len(consistent), "conflicting": len(conflicting)}, indent=2))


if __name__ == "__main__":
    sys.exit(main())
