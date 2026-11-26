#!/usr/bin/env python3
"""Isi security_events (SASL failure & injeksi log) dari semua log yang ada. Idempoten (UNIQUE)."""
import os
import sys
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ZMT_NO_SYNC", "1")
import db  # noqa: E402
from parser import stream_parse_log  # noqa: E402

LOG_DIR = os.getenv("ZMT_LOG_DIR", "/var/log")


def main():
    db.init_db()
    files = sorted((os.path.join(LOG_DIR, f) for f in os.listdir(LOG_DIR)
                    if f.startswith(("zimbra.log", "maillog", "mail.log"))), key=os.path.getmtime)
    total = new = 0
    for fp in files:
        sink = {}
        for _ in stream_parse_log(fp, 0, meta_sink=sink):
            pass
        ev = sink.get("events", [])
        total += len(ev)
        new += db.insert_security_events(ev)
    conn = db.get_db()
    by = dict(conn.execute("SELECT kind, COUNT(*) FROM security_events GROUP BY kind").fetchall())
    ips = conn.execute("SELECT COUNT(DISTINCT ip) FROM security_events").fetchone()[0]
    conn.close()
    print(json.dumps({"files": len(files), "events_seen": total, "inserted": new, "by_kind": by, "distinct_ips": ips}, indent=2))


if __name__ == "__main__":
    main()
