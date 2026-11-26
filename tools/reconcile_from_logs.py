#!/usr/bin/env python3
"""
ZMT one-time reconciliation from raw logs (idempotent).

Re-parses ALL /var/log/mail.log* and /var/log/zimbra.log* (read-only) with the
current parser and merges the evidence into the target DB:
  * inserts delivery records proven by the logs but missing from the DB
  * fills EMPTY fields only (message_id fallback, sender, timestamp, size, SASL user)
  * fills EMPTY Subjects only, from amavis log lines keyed by Queue-ID / Message-ID
  * corrects main_class from the official amavis verdict (Passed/Blocked CLEAN|SPAM...)
Existing non-empty Subjects are never changed. No rows are deleted.
Every Subject filled is logged in subject_recovery_log (source=log_amavis, confidence=1.0).

Usage:
  ZMT_DB=/path/copy.db python3 tools/reconcile_from_logs.py            # dry-run (default)
  ZMT_DB=/etc/zimbra-mail-tracker/tracker.db python3 tools/reconcile_from_logs.py --apply --report out.csv
"""
import os
import sys
import csv
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ZMT_NO_SYNC", "1")
import db  # noqa: E402
from parser import stream_parse_log  # noqa: E402

LOG_DIR = os.getenv("ZMT_LOG_DIR", "/var/log")
PREFIXES = ("zimbra.log", "maillog", "mail.log")
COLS = ["message_id", "sender", "timestamp", "size", "auth_username", "auth_domain", "identity",
        "subject", "main_class"]


def log_files():
    fs = [os.path.join(LOG_DIR, f) for f in os.listdir(LOG_DIR) if f.startswith(PREFIXES)]
    fs = [f for f in fs if os.path.isfile(f)]
    fs.sort(key=os.path.getmtime)
    return fs


def snapshot(conn):
    return {r["id"]: dict(r) for r in conn.execute("SELECT * FROM emails")}


def stats(conn):
    q = lambda s: conn.execute(s).fetchone()[0]
    return {
        "rows": q("SELECT COUNT(*) FROM emails"),
        "rows_excluding_internal_hops": q(f"SELECT COUNT(*) FROM emails WHERE NOT {db.INTERNAL_HOP_SQL}"),
        "internal_hops": q(f"SELECT COUNT(*) FROM emails WHERE {db.INTERNAL_HOP_SQL}"),
        "with_subject": q("SELECT COUNT(*) FROM emails WHERE COALESCE(TRIM(subject),'') <> ''"),
        "with_subject_excluding_internal": q(f"SELECT COUNT(*) FROM emails WHERE COALESCE(TRIM(subject),'') <> '' AND NOT {db.INTERNAL_HOP_SQL}"),
        "distinct_msgid_with_subject": q("SELECT COUNT(DISTINCT message_id) FROM emails WHERE COALESCE(TRIM(subject),'') <> ''"),
        "msgid_is_queue_fallback": q("SELECT COUNT(*) FROM emails WHERE message_id = queue_id"),
        "empty_sender": q("SELECT COUNT(*) FROM emails WHERE COALESCE(sender,'') = ''"),
        "empty_timestamp": q("SELECT COUNT(*) FROM emails WHERE COALESCE(timestamp,'') = ''"),
        "spam": q("SELECT COUNT(*) FROM emails WHERE main_class = 'spam'"),
        "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run, rolled back)")
    ap.add_argument("--report", help="CSV path for per-row Subject changes")
    args = ap.parse_args()

    db.init_db()
    conn = db.get_db()
    before_stats = stats(conn)
    before = snapshot(conn)

    records, sink = [], {}
    per_file = {}
    for fp in log_files():
        n = 0
        for batch, _ in stream_parse_log(fp, 0, meta_sink=sink):
            records.extend(batch)
            n += len(batch)
        per_file[os.path.basename(fp)] = n
    new_records = db.count_new_records(records)

    # Everything below runs on the target DB. In dry-run we work on the same DB
    # but inside a SAVEPOINT that is rolled back at the end.
    conn.close()
    if not args.apply:
        import shutil, tempfile
        tmp = tempfile.mkdtemp()
        shutil.copy2(db.DB_PATH, os.path.join(tmp, "dry.db"))
        db.DB_PATH = os.path.join(tmp, "dry.db")

    db.insert_records(records)
    db.upsert_queue_meta(sink)
    enrich = db.enrich_from_meta()

    conn = db.get_db()
    after = snapshot(conn)
    after_stats = stats(conn)

    changes = {c: 0 for c in COLS}
    subj_rows = []
    overwritten_subjects = 0
    for i, a in after.items():
        b = before.get(i)
        if b is None:
            continue
        for c in COLS:
            if (b.get(c) or "") != (a.get(c) or ""):
                changes[c] += 1
        if (b.get("subject") or "").strip() and (b.get("subject") or "") != (a.get("subject") or ""):
            overwritten_subjects += 1
        if not (b.get("subject") or "").strip() and (a.get("subject") or "").strip():
            subj_rows.append(a)
    new_rows = [a for i, a in after.items() if i not in before]
    new_with_subject = sum(1 for a in new_rows if (a.get("subject") or "").strip())

    if args.apply and subj_rows:
        with conn:
            for a in subj_rows:
                conn.execute("""INSERT INTO subject_recovery_log
                    (email_id, message_id, old_subject, new_subject, source, correlation_method, confidence)
                    VALUES (?, ?, '', ?, 'log_amavis', 'queue_id_or_message_id', 1.0)""",
                             (a["id"], a["message_id"], a["subject"]))

    if args.report:
        with open(args.report, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["email_id", "queue_id", "message_id", "recipient", "status", "timestamp", "new_subject", "source"])
            for a in subj_rows:
                w.writerow([a["id"], a["queue_id"], a["message_id"], a["recipient"], a["status"],
                            a["timestamp"], a["subject"], "log_amavis"])
        os.chmod(args.report, 0o600)

    out = {
        "mode": "APPLY" if args.apply else "DRY-RUN",
        "db": db.DB_PATH if args.apply else "(temporary copy)",
        "parsed_records_per_file": per_file,
        "parsed_records_total": len(records),
        "new_records_inserted": len(new_rows),
        "new_records_with_subject": new_with_subject,
        "queue_meta_entries": len(sink.get("queues", {})),
        "log_subjects_by_message_id": len(sink.get("subjects", {})),
        "existing_rows_changed_by_column": changes,
        "existing_subjects_filled_from_log": len(subj_rows),
        "existing_subjects_overwritten": overwritten_subjects,
        "enrichment_pass": enrich,
        "before": before_stats,
        "after": after_stats,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    conn.close()
    if overwritten_subjects:
        print("ERROR: existing subjects changed - this must never happen", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
