#!/usr/bin/env python3
"""
Fill EMPTY Subjects in tracker.db from Zimbra blob headers, matched by exact Message-ID.

Input: JSON produced by tools/scan_store_headers.py (only Message-IDs whose every
stored copy carries the same Subject are used). Rows whose message_id is only the
queue-id fallback are skipped. Non-empty Subjects are never touched. All updates
run in ONE transaction and each one is logged in subject_recovery_log
(source=zimbra_store_header, correlation_method=message_id_exact, confidence=1.0).

Usage:
  ZMT_DB=... python3 tools/recover_subjects_from_store.py --store store_subjects.json [--apply] [--report out.csv]
"""
import os
import sys
import csv
import json
import sqlite3
import argparse

DB_PATH = os.getenv("ZMT_DB", "/etc/zimbra-mail-tracker/tracker.db")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report")
    a = ap.parse_args()
    data = json.load(open(a.store, encoding="utf-8"))
    store = {m: " ".join(s.split()) for m, s in data["consistent"].items() if s and s.strip()}

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    cand = conn.execute("""SELECT id, queue_id, message_id, recipient, status, timestamp FROM emails
                           WHERE COALESCE(TRIM(subject),'') = '' AND COALESCE(message_id,'') <> ''
                             AND message_id <> queue_id""").fetchall()
    todo = [(r, store[r["message_id"]]) for r in cand if r["message_id"] in store]
    before = conn.execute("SELECT COUNT(*) FROM emails WHERE COALESCE(TRIM(subject),'') <> ''").fetchone()[0]

    updated = 0
    if a.apply and todo:
        try:
            conn.execute("BEGIN IMMEDIATE")
            for r, subj in todo:
                c = conn.execute("UPDATE emails SET subject = ? WHERE id = ? AND COALESCE(TRIM(subject),'') = ''",
                                 (subj, r["id"]))
                if c.rowcount == 1:
                    updated += 1
                    conn.execute("""INSERT INTO subject_recovery_log
                        (email_id, message_id, old_subject, new_subject, source, correlation_method, confidence)
                        VALUES (?, ?, '', ?, 'zimbra_store_header', 'message_id_exact', 1.0)""",
                                 (r["id"], r["message_id"], subj))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    after = conn.execute("SELECT COUNT(*) FROM emails WHERE COALESCE(TRIM(subject),'') <> ''").fetchone()[0]

    if a.report:
        fd = os.open(a.report, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["email_id", "queue_id", "message_id", "recipient", "status", "timestamp", "new_subject"])
            for r, subj in todo:
                w.writerow([r["id"], r["queue_id"], r["message_id"], r["recipient"], r["status"], r["timestamp"], subj])

    print(json.dumps({
        "mode": "APPLY" if a.apply else "DRY-RUN",
        "empty_subject_rows_with_real_message_id": len(cand),
        "matched_in_store_headers": len(todo),
        "updated": updated,
        "subjects_before": before,
        "subjects_after": after,
        "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
    }, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
