#!/usr/bin/env python3
"""
One-time / on-demand backfill of message_meta (To, Cc, attachment metadata) from
Zimbra blobs, read-only. Also fills EMPTY Subjects by exact Message-ID (logged).
The server keeps it up to date afterwards (background scan every 10 minutes).

Usage: ZMT_DB=/etc/zimbra-mail-tracker/tracker.db python3 tools/backfill_message_meta.py --since 2026-10-24
"""
import os
import sys
import json
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db  # noqa: E402
import store_meta  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="YYYY-MM-DD (blob mtime)")
    a = ap.parse_args()
    db.init_db()
    since = time.mktime(time.strptime(a.since, "%Y-%m-%d"))
    t0 = time.time()
    meta, st = store_meta.scan(since)
    n = db.upsert_message_meta(meta)
    filled = db.fill_subjects_from_meta()
    prev = float(db.kv_get("store_scan_since", 0) or 0)
    db.kv_set("store_scan_since", max(prev, st["newest_mtime"]))
    print(json.dumps({"files": st["files"], "errors": st["errors"], "messages": n,
                      "with_cc": sum(1 for m in meta.values() if m["cc"]),
                      "with_attachments": sum(1 for m in meta.values() if m["attachments"]),
                      "subjects_filled": filled, "seconds": round(time.time() - t0, 1)}, indent=2))


if __name__ == "__main__":
    main()
