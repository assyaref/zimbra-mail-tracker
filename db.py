"""
Zimbra Mail Tracker - SQLite Database Engine
Menyimpan riwayat tracking email secara persistent, terindeks, dan cepat.
"""

import sqlite3
import os

DB_PATH = os.getenv("ZMT_DB", "/etc/zimbra-mail-tracker/tracker.db")

def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    with conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT,
            queue_id TEXT,
            timestamp TEXT,
            auth_domain TEXT,
            auth_username TEXT,
            identity TEXT,
            sender TEXT,
            recipient TEXT,
            main_class TEXT,
            subject TEXT,
            status TEXT,
            relay TEXT,
            reason TEXT,
            size INTEGER,
            UNIQUE(queue_id, recipient, status)
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_ts ON emails(timestamp DESC);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_sender ON emails(sender);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_recip ON emails(recipient);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_status ON emails(status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_class ON emails(main_class);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_domain ON emails(auth_domain);")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS file_offsets (
            filepath TEXT PRIMARY KEY,
            last_pos INTEGER,
            last_mtime REAL
        );
        """)
    conn.close()

def insert_records(records):
    """Insert batch email records ignoring duplicate queue_id + recipient + status."""
    if not records:
        return 0
    conn = get_db()
    inserted = 0
    with conn:
        cursor = conn.cursor()
        for r in records:
            try:
                cursor.execute("""
                INSERT OR IGNORE INTO emails (
                    message_id, queue_id, timestamp, auth_domain, auth_username,
                    identity, sender, recipient, main_class, subject, status,
                    relay, reason, size
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    r.get("message_id") or r.get("queue_id", ""),
                    r.get("queue_id", ""),
                    r.get("timestamp", ""),
                    r.get("auth_domain", ""),
                    r.get("auth_username", ""),
                    r.get("identity", ""),
                    r.get("from", ""),
                    r.get("to", ""),
                    r.get("main_class", "not-spam"),
                    r.get("subject", ""),
                    r.get("status", "unknown"),
                    r.get("relay", ""),
                    r.get("reason", ""),
                    r.get("size", 0)
                ))
                if cursor.rowcount > 0:
                    inserted += 1
            except Exception:
                pass
    conn.close()
    return inserted

def query_emails(q="", status="", main_class="", auth_domain="", date_from="", date_to="",
                 sort_col="timestamp", sort_dir="DESC", page=1, per_page=50):
    conn = get_db()
    cursor = conn.cursor()

    where_clauses = []
    params = []

    if q:
        where_clauses.append("""(
            sender LIKE ? OR recipient LIKE ? OR subject LIKE ? OR
            message_id LIKE ? OR queue_id LIKE ? OR auth_username LIKE ?
        )""")
        param_q = f"%{q}%"
        params.extend([param_q] * 6)

    if status:
        where_clauses.append("status = ?")
        params.append(status)

    if main_class:
        where_clauses.append("main_class = ?")
        params.append(main_class)

    if auth_domain:
        where_clauses.append("auth_domain LIKE ?")
        params.append(f"%{auth_domain}%")

    if date_from:
        where_clauses.append("timestamp >= ?")
        params.append(date_from)

    if date_to:
        where_clauses.append("timestamp <= ?")
        params.append(date_to + " 23:59:59")

    where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    # Total count
    cursor.execute(f"SELECT COUNT(*) as count FROM emails{where_sql}", params)
    total = cursor.fetchone()["count"]

    # Valid sort column
    valid_cols = {
        "message_id": "message_id",
        "timestamp": "timestamp",
        "auth_domain": "auth_domain",
        "auth_username": "auth_username",
        "identity": "identity",
        "from": "sender",
        "sender": "sender",
        "to": "recipient",
        "recipient": "recipient",
        "main_class": "main_class",
        "subject": "subject",
        "status": "status",
        "size": "size"
    }
    col = valid_cols.get(sort_col, "timestamp")
    order = "ASC" if str(sort_dir).upper() == "ASC" else "DESC"

    offset = (page - 1) * per_page
    cursor.execute(f"""
        SELECT * FROM emails
        {where_sql}
        ORDER BY {col} {order}
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])

    rows = []
    for r in cursor.fetchall():
        d = dict(r)
        d["from"] = d.get("sender", "")
        d["to"] = d.get("recipient", "")
        rows.append(d)

    # Stats
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status IN ('delivered', 'sent') THEN 1 ELSE 0 END) as delivered,
            SUM(CASE WHEN status = 'bounced' THEN 1 ELSE 0 END) as bounced,
            SUM(CASE WHEN status = 'deferred' THEN 1 ELSE 0 END) as deferred,
            SUM(CASE WHEN main_class = 'spam' THEN 1 ELSE 0 END) as spam
        FROM emails
    """)
    stats_row = cursor.fetchone()
    stats = {
        "total": stats_row["total"] or 0,
        "delivered": stats_row["delivered"] or 0,
        "bounced": stats_row["bounced"] or 0,
        "deferred": stats_row["deferred"] or 0,
        "spam": stats_row["spam"] or 0,
    }

    conn.close()
    return rows, total, stats
