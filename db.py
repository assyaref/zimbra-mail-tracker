"""
Zimbra Mail Tracker - SQLite Database Engine

Database produksi tunggal: /etc/zimbra-mail-tracker/tracker.db (override: ZMT_DB).
"""

import os
import re
import json
import sqlite3
import logging

DB_PATH = os.getenv("ZMT_DB", "/etc/zimbra-mail-tracker/tracker.db")
log = logging.getLogger("zmt.db")

# Hop internal content-filter (postfix -> amavis -> postfix) tercatat sebagai
# status=sent ke 127.0.0.1/::1. Itu bukan pengiriman ke penerima, jadi
# disembunyikan dari tampilan/statistik secara default (data tetap disimpan).
INTERNAL_HOP_SQL = (
    "(status = 'sent' AND (relay LIKE '127.%' OR relay LIKE 'localhost%' "
    "OR relay LIKE '[::1]%' OR relay LIKE '::1%'))"
)


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000;")
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
        try:
            conn.execute("ALTER TABLE emails ADD COLUMN subject TEXT DEFAULT '';")
        except sqlite3.OperationalError:
            pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_ts ON emails(timestamp DESC);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_sender ON emails(sender);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_recip ON emails(recipient);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_status ON emails(status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_class ON emails(main_class);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_domain ON emails(auth_domain);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_subject ON emails(subject);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_msgid ON emails(message_id);")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS file_offsets (
            filepath TEXT PRIMARY KEY,
            last_pos INTEGER,
            last_mtime REAL
        );
        """)
        try:
            conn.execute("ALTER TABLE file_offsets ADD COLUMN inode INTEGER;")
        except sqlite3.OperationalError:
            pass

        # Metadata per queue yang terbukti dari log (dipakai untuk korelasi lintas siklus sync).
        conn.execute("""
        CREATE TABLE IF NOT EXISTS queue_meta (
            queue_id TEXT PRIMARY KEY,
            message_id TEXT DEFAULT '',
            sender TEXT DEFAULT '',
            size INTEGER DEFAULT 0,
            timestamp TEXT DEFAULT '',
            auth_username TEXT DEFAULT '',
            auth_domain TEXT DEFAULT '',
            subject TEXT DEFAULT '',
            verdict TEXT DEFAULT '',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_qmeta_msgid ON queue_meta(message_id);")
        # Metadata dari header/MIME blob Zimbra (To, Cc, lampiran). Isi lampiran tidak disimpan.
        conn.execute("""
        CREATE TABLE IF NOT EXISTS message_meta (
            message_id TEXT PRIMARY KEY,
            hdr_to TEXT DEFAULT '[]',
            hdr_cc TEXT DEFAULT '[]',
            attachments TEXT,
            att_count INTEGER,
            att_total INTEGER,
            store_subject TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS limits_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now','localtime')),
            username TEXT, ip TEXT, ok INTEGER,
            changes TEXT, warnings TEXT, log TEXT
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS security_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, kind TEXT NOT NULL, ip TEXT DEFAULT '', detail TEXT DEFAULT '',
            UNIQUE(ts, kind, ip, detail)
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_secev_ts ON security_events(ts);")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS kv (
            k TEXT PRIMARY KEY,
            v TEXT
        );
        """)
        # Subject per Message-ID dari baris amavis (sumber log terverifikasi).
        conn.execute("""
        CREATE TABLE IF NOT EXISTS message_subjects (
            message_id TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)
    conn.close()
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass


# ── Offsets (inode-aware, tahan rotasi log) ───────────────────────────────────

def get_offset(conn, filepath, inode, size):
    """Kembalikan offset aman untuk file plain.

    - inode sama dengan catatan path -> lanjut dari offset tersimpan
    - file hasil rotasi (mis. mail.log -> mail.log.1) -> pakai offset inode lama
    - file baru / terpotong (size < offset) -> mulai dari 0
    """
    row = conn.execute("SELECT last_pos, inode FROM file_offsets WHERE filepath = ?", (filepath,)).fetchone()
    if row and row["inode"] == inode:
        pos = row["last_pos"] or 0
    else:
        other = conn.execute(
            "SELECT last_pos FROM file_offsets WHERE inode = ? AND filepath NOT LIKE '%.gz' "
            "ORDER BY last_pos DESC LIMIT 1", (inode,)
        ).fetchone()
        if other:
            pos = other["last_pos"] or 0
        elif row and row["inode"] is None:
            # Catatan lama (sebelum kolom inode ada): percayai hanya jika masih masuk akal.
            pos = row["last_pos"] or 0
        else:
            pos = 0
    if pos > size:
        pos = 0
    return pos


def set_offset(conn, filepath, pos, mtime, inode):
    conn.execute("""
        INSERT INTO file_offsets (filepath, last_pos, last_mtime, inode) VALUES (?, ?, ?, ?)
        ON CONFLICT(filepath) DO UPDATE SET last_pos=excluded.last_pos,
            last_mtime=excluded.last_mtime, inode=excluded.inode
    """, (filepath, pos, mtime, inode))


# ── Insert ────────────────────────────────────────────────────────────────────

_UPSERT_SQL = """
INSERT INTO emails (
    message_id, queue_id, timestamp, auth_domain, auth_username,
    identity, sender, recipient, main_class, subject, status,
    relay, reason, size
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(queue_id, recipient, status) DO UPDATE SET
    -- Subject yang sudah ada TIDAK pernah ditimpa.
    subject       = CASE WHEN COALESCE(emails.subject,'') = '' THEN excluded.subject ELSE emails.subject END,
    -- Message-ID asli tidak boleh diganti fallback queue_id.
    message_id    = CASE WHEN (COALESCE(emails.message_id,'') = '' OR emails.message_id = emails.queue_id)
                              AND COALESCE(excluded.message_id,'') <> '' THEN excluded.message_id
                         ELSE emails.message_id END,
    sender        = CASE WHEN COALESCE(emails.sender,'') = '' THEN excluded.sender ELSE emails.sender END,
    size          = CASE WHEN COALESCE(emails.size,0) = 0 THEN excluded.size ELSE emails.size END,
    auth_username = CASE WHEN COALESCE(emails.auth_username,'') = ''
                              OR (emails.auth_username = emails.sender AND excluded.auth_username <> excluded.sender
                                  AND COALESCE(excluded.auth_username,'') <> '')
                         THEN excluded.auth_username ELSE emails.auth_username END,
    auth_domain   = CASE WHEN COALESCE(emails.auth_domain,'') = ''
                              OR (emails.auth_username = emails.sender AND excluded.auth_username <> excluded.sender
                                  AND COALESCE(excluded.auth_domain,'') <> '')
                         THEN excluded.auth_domain ELSE emails.auth_domain END,
    identity      = CASE WHEN COALESCE(emails.identity,'') = '' THEN excluded.identity ELSE emails.identity END,
    relay         = CASE WHEN COALESCE(emails.relay,'') = '' THEN excluded.relay ELSE emails.relay END,
    reason        = CASE WHEN COALESCE(emails.reason,'') = '' THEN excluded.reason ELSE emails.reason END,
    main_class    = CASE WHEN excluded.main_class = 'spam' THEN 'spam' ELSE emails.main_class END,
    timestamp     = CASE WHEN COALESCE(emails.timestamp,'') = '' THEN excluded.timestamp ELSE emails.timestamp END
"""


def insert_records(records):
    """Insert/merge records. Return jumlah baris yang tersentuh (insert atau update)."""
    if not records:
        return 0
    conn = get_db()
    inserted = 0
    errors = 0
    first_err = None
    with conn:
        cursor = conn.cursor()
        for r in records:
            try:
                cursor.execute(_UPSERT_SQL, (
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
                    r.get("size", 0) or 0,
                ))
                if cursor.rowcount > 0:
                    inserted += 1
            except sqlite3.Error as e:
                errors += 1
                first_err = first_err or e
    conn.close()
    if errors:
        log.error("insert_records: %d record gagal (contoh: %s)", errors, first_err)
    return inserted


def count_new_records(records):
    """Hitung record yang belum ada (untuk dry-run)."""
    conn = get_db()
    n = 0
    for r in records:
        if not conn.execute("SELECT 1 FROM emails WHERE queue_id=? AND recipient=? AND status=?",
                            (r.get("queue_id", ""), r.get("to", ""), r.get("status", ""))).fetchone():
            n += 1
    conn.close()
    return n


# ── Queue metadata & enrichment ───────────────────────────────────────────────

def upsert_queue_meta(sink, conn=None):
    """Simpan meta_sink dari parser. Nilai yang sudah ada tidak ditimpa nilai kosong;
    subject per Message-ID tidak ditimpa jika sudah tercatat."""
    if not sink:
        return 0
    own = conn is None
    conn = conn or get_db()
    n = 0
    with conn:
        for qid, m in sink.get("queues", {}).items():
            conn.execute("""
                INSERT INTO queue_meta (queue_id, message_id, sender, size, timestamp,
                                        auth_username, auth_domain, subject, verdict)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(queue_id) DO UPDATE SET
                    message_id    = CASE WHEN queue_meta.message_id = '' THEN excluded.message_id ELSE queue_meta.message_id END,
                    sender        = CASE WHEN queue_meta.sender = '' THEN excluded.sender ELSE queue_meta.sender END,
                    size          = CASE WHEN queue_meta.size = 0 THEN excluded.size ELSE queue_meta.size END,
                    timestamp     = CASE WHEN queue_meta.timestamp = '' THEN excluded.timestamp ELSE queue_meta.timestamp END,
                    auth_username = CASE WHEN queue_meta.auth_username = '' THEN excluded.auth_username ELSE queue_meta.auth_username END,
                    auth_domain   = CASE WHEN queue_meta.auth_domain = '' THEN excluded.auth_domain ELSE queue_meta.auth_domain END,
                    subject       = CASE WHEN queue_meta.subject = '' THEN excluded.subject ELSE queue_meta.subject END,
                    verdict       = CASE WHEN excluded.verdict <> '' THEN excluded.verdict ELSE queue_meta.verdict END,
                    updated_at    = CURRENT_TIMESTAMP
            """, (qid, m.get("message_id", ""), m.get("sender", ""), m.get("size", 0) or 0,
                  m.get("timestamp", ""), m.get("auth_username", ""), m.get("auth_domain", ""),
                  m.get("subject", ""), m.get("verdict", "")))
            n += 1
        for msgid, subj in sink.get("subjects", {}).items():
            if msgid and subj:
                conn.execute("INSERT OR IGNORE INTO message_subjects (message_id, subject) VALUES (?, ?)",
                             (msgid, subj))
    if own:
        conn.close()
    return n


def enrich_from_meta(conn=None):
    """Lengkapi field kosong di emails memakai bukti log (queue_meta/message_subjects).
    Tidak pernah menimpa Subject yang sudah terisi. Return dict jumlah perubahan."""
    own = conn is None
    conn = conn or get_db()
    res = {}
    with conn:
        c = conn.execute("""
            UPDATE emails SET message_id = qm.message_id
            FROM queue_meta qm
            WHERE qm.queue_id = emails.queue_id AND qm.message_id <> ''
              AND (COALESCE(emails.message_id,'') = '' OR emails.message_id = emails.queue_id)
        """); res["message_id"] = c.rowcount
        c = conn.execute("""
            UPDATE emails SET sender = qm.sender,
                   identity = CASE WHEN COALESCE(emails.identity,'') = '' THEN qm.sender ELSE emails.identity END,
                   auth_username = CASE WHEN COALESCE(emails.auth_username,'') = '' THEN qm.sender ELSE emails.auth_username END,
                   auth_domain = CASE WHEN COALESCE(emails.auth_domain,'') = '' AND instr(qm.sender,'@') > 0
                                      THEN substr(qm.sender, instr(qm.sender,'@') + 1) ELSE emails.auth_domain END
            FROM queue_meta qm
            WHERE qm.queue_id = emails.queue_id AND qm.sender <> '' AND COALESCE(emails.sender,'') = ''
        """); res["sender"] = c.rowcount
        c = conn.execute("""
            UPDATE emails SET timestamp = qm.timestamp FROM queue_meta qm
            WHERE qm.queue_id = emails.queue_id AND qm.timestamp <> '' AND COALESCE(emails.timestamp,'') = ''
        """); res["timestamp"] = c.rowcount
        c = conn.execute("""
            UPDATE emails SET size = qm.size FROM queue_meta qm
            WHERE qm.queue_id = emails.queue_id AND qm.size > 0 AND COALESCE(emails.size,0) = 0
        """); res["size"] = c.rowcount
        # SASL user (identitas pengirim sebenarnya) per Message-ID, hanya bila unik.
        tc = conn.total_changes
        conn.execute("""
            WITH a AS (
                SELECT message_id, MIN(auth_username) AS u FROM queue_meta
                WHERE message_id <> '' AND auth_username <> ''
                GROUP BY message_id HAVING COUNT(DISTINCT auth_username) = 1
            )
            UPDATE emails SET auth_username = a.u,
                   auth_domain = substr(a.u, instr(a.u,'@') + 1),
                   identity = CASE WHEN COALESCE(emails.identity,'') IN ('', emails.sender) THEN a.u ELSE emails.identity END
            FROM a
            WHERE a.message_id = emails.message_id AND emails.message_id <> emails.queue_id
              AND COALESCE(emails.auth_username,'') IN ('', emails.sender) AND emails.auth_username IS NOT a.u
        """); res["auth_username"] = conn.total_changes - tc
        # Subject: hanya isi yang kosong, dari bukti log (queue lalu Message-ID).
        c = conn.execute("""
            UPDATE emails SET subject = qm.subject FROM queue_meta qm
            WHERE qm.queue_id = emails.queue_id AND qm.subject <> '' AND COALESCE(emails.subject,'') = ''
        """); res["subject_by_queue"] = c.rowcount
        c = conn.execute("""
            UPDATE emails SET subject = ms.subject FROM message_subjects ms
            WHERE ms.message_id = emails.message_id AND emails.message_id <> emails.queue_id
              AND COALESCE(emails.subject,'') = ''
        """); res["subject_by_msgid"] = c.rowcount
        # Klasifikasi spam berdasarkan verdict resmi amavis (bukan Hits > 0).
        c = conn.execute("""
            UPDATE emails SET main_class = CASE WHEN qm.verdict IN ('SPAM','SPAMMY') THEN 'spam' ELSE 'not-spam' END
            FROM queue_meta qm
            WHERE qm.queue_id = emails.queue_id AND qm.verdict <> ''
              AND emails.main_class IS NOT (CASE WHEN qm.verdict IN ('SPAM','SPAMMY') THEN 'spam' ELSE 'not-spam' END)
        """); res["main_class"] = c.rowcount
    if own:
        conn.close()
    return res


# ── Query ─────────────────────────────────────────────────────────────────────

def _like(s):
    return "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def query_emails(q="", status="", main_class="", auth_domain="", date_from="", date_to="",
                 sort_col="timestamp", sort_dir="DESC", page=1, per_page=50, include_internal=False):
    conn = get_db()
    cursor = conn.cursor()

    where_clauses = []
    params = []

    if not include_internal:
        where_clauses.append(f"NOT {INTERNAL_HOP_SQL}")

    if q:
        where_clauses.append("""(
            sender LIKE ? ESCAPE '\\' OR recipient LIKE ? ESCAPE '\\' OR subject LIKE ? ESCAPE '\\' OR
            message_id LIKE ? ESCAPE '\\' OR queue_id LIKE ? ESCAPE '\\' OR auth_username LIKE ? ESCAPE '\\'
        )""")
        params.extend([_like(q)] * 6)

    if status:
        where_clauses.append("status = ?")
        params.append(status)

    if main_class:
        where_clauses.append("main_class = ?")
        params.append(main_class)

    if auth_domain:
        where_clauses.append("auth_domain LIKE ? ESCAPE '\\'")
        params.append(_like(auth_domain))

    if date_from:
        where_clauses.append("timestamp >= ?")
        params.append(date_from)

    if date_to:
        where_clauses.append("timestamp <= ?")
        params.append(date_to if len(date_to) > 10 else date_to + " 23:59:59")

    where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    cursor.execute(f"SELECT COUNT(*) as count FROM emails{where_sql}", params)
    total = cursor.fetchone()["count"]

    valid_cols = {
        "message_id": "message_id", "timestamp": "timestamp",
        "auth_domain": "auth_domain", "auth_username": "auth_username",
        "identity": "identity", "from": "sender", "sender": "sender",
        "to": "recipient", "recipient": "recipient", "main_class": "main_class",
        "subject": "subject", "status": "status", "size": "size"
    }
    col = valid_cols.get(sort_col, "timestamp")
    order = "ASC" if str(sort_dir).upper() == "ASC" else "DESC"

    offset = (page - 1) * per_page
    cursor.execute(f"""
        SELECT * FROM emails
        {where_sql}
        ORDER BY {col} {order}, id {order}
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])

    rows = []
    for r in cursor.fetchall():
        d = dict(r)
        d["from"] = d.get("sender", "")
        d["to"] = d.get("recipient", "")
        rows.append(d)

    base = "" if include_internal else f" WHERE NOT {INTERNAL_HOP_SQL}"
    cursor.execute(f"""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status IN ('delivered', 'sent') THEN 1 ELSE 0 END) as delivered,
            SUM(CASE WHEN status = 'bounced' THEN 1 ELSE 0 END) as bounced,
            SUM(CASE WHEN status = 'deferred' THEN 1 ELSE 0 END) as deferred,
            SUM(CASE WHEN main_class = 'spam' THEN 1 ELSE 0 END) as spam
        FROM emails{base}
    """)
    stats_row = cursor.fetchone()
    stats = {
        "total": stats_row["total"] or 0,
        "delivered": stats_row["delivered"] or 0,
        "bounced": stats_row["bounced"] or 0,
        "deferred": stats_row["deferred"] or 0,
        "spam": stats_row["spam"] or 0,
    }
    stats["internal_hops"] = cursor.execute(f"SELECT COUNT(*) FROM emails WHERE {INTERNAL_HOP_SQL}").fetchone()[0]

    conn.close()
    return rows, total, stats


def backfill_subjects(subject_map: dict):
    """Isi Subject kosong berdasarkan queue_id / Message-ID. Tidak menimpa yang sudah ada."""
    if not subject_map:
        return
    conn = get_db()
    with conn:
        cursor = conn.cursor()
        for key, subject in subject_map.items():
            if not subject:
                continue
            cursor.execute("""
                UPDATE emails SET subject = ?
                WHERE queue_id = ? AND COALESCE(subject, '') = ''
            """, (subject, key))
            cursor.execute("""
                UPDATE emails SET subject = ?
                WHERE message_id = ? AND message_id <> queue_id AND COALESCE(subject, '') = ''
            """, (subject, key))
    conn.close()


# ── Message metadata dari blob Zimbra (To/Cc/lampiran) ───────────────────────

def kv_get(key, default=None):
    conn = get_db()
    row = conn.execute("SELECT v FROM kv WHERE k = ?", (key,)).fetchone()
    conn.close()
    return row["v"] if row else default


def kv_set(key, value):
    conn = get_db()
    with conn:
        conn.execute("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                     (key, str(value)))
    conn.close()


def upsert_message_meta(meta):
    """meta: {message_id: {to, cc, attachments, subject}} dari store_meta.scan().
    Nilai yang sudah ada hanya diganti bila kosong. Return jumlah baris diproses."""
    if not meta:
        return 0
    conn = get_db()
    n = 0
    with conn:
        for mid, m in meta.items():
            atts = m.get("attachments")
            conn.execute("""
                INSERT INTO message_meta (message_id, hdr_to, hdr_cc, attachments, att_count, att_total, store_subject)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    hdr_to = CASE WHEN message_meta.hdr_to IN ('', '[]') THEN excluded.hdr_to ELSE message_meta.hdr_to END,
                    hdr_cc = CASE WHEN message_meta.hdr_cc IN ('', '[]') THEN excluded.hdr_cc ELSE message_meta.hdr_cc END,
                    attachments = COALESCE(message_meta.attachments, excluded.attachments),
                    att_count = COALESCE(message_meta.att_count, excluded.att_count),
                    att_total = COALESCE(message_meta.att_total, excluded.att_total),
                    store_subject = COALESCE(message_meta.store_subject, excluded.store_subject),
                    updated_at = CURRENT_TIMESTAMP
            """, (mid, json.dumps(m.get("to") or [], ensure_ascii=False), json.dumps(m.get("cc") or [], ensure_ascii=False),
                  json.dumps(atts, ensure_ascii=False) if atts is not None else None,
                  len(atts) if atts is not None else None,
                  sum(a.get("size", 0) for a in atts) if atts is not None else None,
                  m.get("subject")))
            n += 1
    conn.close()
    return n


def fill_subjects_from_meta():
    """Isi Subject KOSONG dari header blob (Message-ID exact, salinan konsisten). Dicatat di recovery log."""
    conn = get_db()
    filled = 0
    with conn:
        has_log = conn.execute("SELECT 1 FROM sqlite_master WHERE name='subject_recovery_log'").fetchone()
        rows = conn.execute("""
            SELECT e.id, e.message_id, mm.store_subject FROM emails e JOIN message_meta mm ON mm.message_id = e.message_id
            WHERE COALESCE(TRIM(e.subject),'') = '' AND e.message_id <> e.queue_id
              AND COALESCE(TRIM(mm.store_subject),'') <> ''
        """).fetchall()
        for r in rows:
            c = conn.execute("UPDATE emails SET subject = ? WHERE id = ? AND COALESCE(TRIM(subject),'') = ''",
                             (r["store_subject"], r["id"]))
            if c.rowcount == 1:
                filled += 1
                if has_log:
                    conn.execute("""INSERT INTO subject_recovery_log
                        (email_id, message_id, old_subject, new_subject, source, correlation_method, confidence)
                        VALUES (?, ?, '', ?, 'zimbra_store_header', 'message_id_exact', 1.0)""",
                                 (r["id"], r["message_id"], r["store_subject"]))
    conn.close()
    return filled


def _addr_only(s):
    s = (s or "").strip()
    if "<" in s and s.endswith(">"):
        s = s[s.rfind("<") + 1:-1]
    return s.strip().lower()


def get_message_detail(message_id, queue_id=""):
    """Detail satu pesan: header To/Cc, lampiran, dan semua penerima envelope (tanpa hop internal)."""
    conn = get_db()
    key_is_msgid = bool(message_id) and message_id != queue_id
    out = {"message_id": message_id, "headers_available": False, "to": [], "cc": [],
           "attachments": None, "att_count": None, "att_total": None, "recipients": []}
    if key_is_msgid:
        mm = conn.execute("SELECT * FROM message_meta WHERE message_id = ?", (message_id,)).fetchone()
        if mm:
            out.update(headers_available=True, to=json.loads(mm["hdr_to"] or "[]"), cc=json.loads(mm["hdr_cc"] or "[]"),
                       attachments=json.loads(mm["attachments"]) if mm["attachments"] else
                       ([] if mm["att_count"] == 0 else None),
                       att_count=mm["att_count"], att_total=mm["att_total"])
        rows = conn.execute(f"""SELECT recipient, status, relay, timestamp, reason, queue_id FROM emails
                                WHERE message_id = ? AND NOT {INTERNAL_HOP_SQL} ORDER BY recipient, timestamp""",
                            (message_id,)).fetchall()
    else:
        rows = conn.execute(f"""SELECT recipient, status, relay, timestamp, reason, queue_id FROM emails
                                WHERE queue_id = ? AND NOT {INTERNAL_HOP_SQL} ORDER BY recipient""",
                            (queue_id,)).fetchall()
    conn.close()
    to_set = {_addr_only(a) for a in out["to"]}
    cc_set = {_addr_only(a) for a in out["cc"]}
    seen = {}
    for r in rows:
        addr = (r["recipient"] or "").lower()
        kind = "to" if addr in to_set else "cc" if addr in cc_set else ("bcc" if out["headers_available"] else "")
        seen.setdefault((addr, r["status"]), {
            "recipient": r["recipient"], "status": r["status"], "relay": r["relay"],
            "timestamp": r["timestamp"], "reason": r["reason"], "kind": kind,
        })
    out["recipients"] = list(seen.values())
    return out


def limits_usage(message_limit=None, days=30):
    """Statistik pemakaian terhadap batas ukuran (tanpa hop internal)."""
    conn = get_db()
    since = conn.execute("SELECT datetime('now','localtime', ?)", (f"-{int(days)} days",)).fetchone()[0]
    out = {"days": days}
    r = conn.execute("""
        SELECT mm.message_id, mm.att_total, mm.att_count, MAX(e.subject) AS subject, MAX(e.timestamp) AS ts
        FROM message_meta mm JOIN emails e ON e.message_id = mm.message_id
        WHERE e.timestamp >= ? AND mm.att_total IS NOT NULL
        GROUP BY mm.message_id ORDER BY mm.att_total DESC LIMIT 1
    """, (since,)).fetchone()
    out["largest_attachments"] = dict(r) if r else None
    out["messages_with_attachments"] = conn.execute("""
        SELECT COUNT(DISTINCT mm.message_id) FROM message_meta mm JOIN emails e ON e.message_id = mm.message_id
        WHERE e.timestamp >= ? AND mm.att_count > 0""", (since,)).fetchone()[0]
    out["largest_message_size"] = conn.execute(
        f"SELECT MAX(size) FROM emails WHERE timestamp >= ? AND NOT {INTERNAL_HOP_SQL}", (since,)).fetchone()[0]
    out["near_limit_messages"] = conn.execute(
        f"SELECT COUNT(DISTINCT message_id) FROM emails WHERE timestamp >= ? AND size >= ? AND NOT {INTERNAL_HOP_SQL}",
        (since, int(message_limit * 0.8))).fetchone()[0] if message_limit else None
    out["size_rejections"] = conn.execute("""
        SELECT COUNT(*) FROM emails WHERE timestamp >= ? AND status IN ('bounced','deferred') AND (
            reason LIKE '%5.3.4%' OR reason LIKE '%exceeds size limit%' OR reason LIKE '%message size%'
            OR reason LIKE '%too large%' OR reason LIKE '%file too big%')""", (since,)).fetchone()[0]
    conn.close()
    return out


def add_limits_audit(username, ip, ok, changes, warnings, log_lines):
    conn = get_db()
    with conn:
        conn.execute("INSERT INTO limits_audit (username, ip, ok, changes, warnings, log) VALUES (?, ?, ?, ?, ?, ?)",
                     (username, ip, 1 if ok else 0, json.dumps(changes, ensure_ascii=False),
                      json.dumps(warnings, ensure_ascii=False), json.dumps(log_lines, ensure_ascii=False)))
    conn.close()


def list_limits_audit(limit=10):
    conn = get_db()
    rows = conn.execute("SELECT * FROM limits_audit ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("changes", "warnings", "log"):
            try:
                d[k] = json.loads(d[k] or "[]")
            except ValueError:
                d[k] = []
        out.append(d)
    return out


# ── Security events ───────────────────────────────────────────────────────────

def insert_security_events(events, conn=None):
    """events: [(kind, datetime|None, ip, detail)]. Duplikat (mail.log vs zimbra.log) diabaikan."""
    if not events:
        return 0
    own = conn is None
    conn = conn or get_db()
    n = 0
    with conn:
        for kind, ts, ip, detail in events:
            if ts is None:
                continue
            c = conn.execute("INSERT OR IGNORE INTO security_events (ts, kind, ip, detail) VALUES (?, ?, ?, ?)",
                             (ts.strftime("%Y-%m-%d %H:%M:%S"), kind, ip or "", detail or ""))
            n += c.rowcount
    if own:
        conn.close()
    return n


def prune_security_events(days=90):
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM security_events WHERE ts < datetime('now','localtime', ?)", (f"-{int(days)} days",))
    conn.close()


# ── Traffic / statistik ───────────────────────────────────────────────────────

def traffic_by_day(days=7):
    """Per hari (lokal): delivered (sent+delivered, tanpa hop internal), bounced, deferred, spam."""
    days = max(1, min(int(days), 365))
    conn = get_db()
    rows = conn.execute(f"""
        WITH RECURSIVE d(day, n) AS (
            SELECT date('now','localtime', ?), 1
            UNION ALL SELECT date(day, '+1 day'), n + 1 FROM d WHERE n < ?
        )
        SELECT d.day AS date,
               COALESCE(SUM(CASE WHEN e.status IN ('delivered','sent') THEN 1 END), 0) AS delivered,
               COALESCE(SUM(CASE WHEN e.status = 'bounced' THEN 1 END), 0) AS bounced,
               COALESCE(SUM(CASE WHEN e.status = 'deferred' THEN 1 END), 0) AS deferred,
               COALESCE(SUM(CASE WHEN e.main_class = 'spam' THEN 1 END), 0) AS spam
        FROM d LEFT JOIN emails e ON substr(e.timestamp, 1, 10) = d.day AND NOT {INTERNAL_HOP_SQL.replace('status', 'e.status').replace('relay', 'e.relay')}
        GROUP BY d.day ORDER BY d.day
    """, (f"-{days - 1} days", days)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


_BOUNCE_CATS = [
    ("Loop / konfigurasi MX", ("loops back to myself", "mail loop", "too many hops")),
    ("Mailbox penuh", ("quota", "mailbox full", "mailbox is full", "over quota", "5.2.2", "insufficient storage", "inode limit")),
    ("User tidak dikenal", ("user unknown", "unknown user", "does not exist", "no such user", "5.1.1", "invalid recipient",
                            "mailbox unavailable", "recipient address rejected", "address not found")),
    ("Ukuran pesan", ("5.3.4", "exceeds size", "message size", "too large", "too big")),
    ("Ditolak sebagai spam/kebijakan", ("spam", "blocked", "blacklist", "blocklist", "policy", "5.7.1", "5.7.", "dmarc", "spf",
                                        "dkim", "reputation", "denied")),
    ("DNS / host tidak ditemukan", ("host not found", "name service", "no mx", "domain not found", "nxdomain", "dns")),
    ("Koneksi / timeout", ("timed out", "timeout", "connection refused", "network is unreachable", "lost connection",
                           "connect to", "connection reset")),
    ("TLS", ("tls", "certificate", "ssl")),
]
_RE_BARE_DOMAIN = re.compile(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def categorize_reason(reason):
    r = (reason or "").strip()
    if _RE_BARE_DOMAIN.match(r):
        # postfix/error dengan alasan berupa nama domain: transport/akun tujuan salah konfigurasi
        return "Error transport lokal (cek akun/transport)"
    low = r.lower()
    if "said:" in low:
        low = low.split("said:", 1)[1]   # abaikan nama host remote (mis. mx.spamexperts.com)
    for name, keys in _BOUNCE_CATS:
        if any(k in low for k in keys):
            return name
    return "Lainnya"


def _domain_sql(col):
    return f"lower(substr({col}, instr({col}, '@') + 1))"


def stats_overview(days=30, local_domains=None):
    days = max(1, min(int(days), 365))
    conn = get_db()
    since = conn.execute("SELECT datetime('now','localtime', ?)", (f"-{days} days",)).fetchone()[0]
    base = f"FROM emails WHERE timestamp >= ? AND NOT {INTERNAL_HOP_SQL}"
    q1 = lambda sql, *a: conn.execute(sql, (since, *a)).fetchone()
    k = q1(f"""SELECT COUNT(*) AS deliveries, COUNT(DISTINCT message_id) AS messages,
                      SUM(status IN ('delivered','sent')) AS ok, SUM(status='bounced') AS bounced,
                      SUM(status='deferred') AS deferred, SUM(main_class='spam') AS spam,
                      SUM(status='delivered') AS local_deliveries, SUM(status='sent') AS external_deliveries,
                      COUNT(DISTINCT sender) AS senders, COUNT(DISTINCT recipient) AS recipients {base}""")
    vol = q1(f"""SELECT SUM(size) AS bytes, AVG(size) AS avg_size, MAX(size) AS max_size FROM (
                   SELECT message_id, MAX(size) AS size {base} GROUP BY message_id)""")
    kpi = dict(k)
    kpi.update({kk: vol[kk] for kk in ("bytes", "avg_size", "max_size")})
    kpi["bounce_rate"] = round(100.0 * (kpi["bounced"] or 0) / kpi["deliveries"], 2) if kpi["deliveries"] else 0.0

    hourly = [0] * 24
    for r in conn.execute(f"SELECT CAST(substr(timestamp, 12, 2) AS INTEGER) AS h, COUNT(DISTINCT message_id) AS n {base} GROUP BY h", (since,)):
        if r["h"] is not None and 0 <= r["h"] < 24:
            hourly[r["h"]] = r["n"]

    top = lambda sql, n=10: [dict(r) for r in conn.execute(sql, (since, n))]
    top_senders = top(f"SELECT sender AS name, COUNT(DISTINCT message_id) AS messages {base} AND sender <> '' GROUP BY sender ORDER BY messages DESC LIMIT ?")
    top_recipients = top(f"SELECT recipient AS name, COUNT(*) AS messages {base} GROUP BY recipient ORDER BY messages DESC LIMIT ?")
    top_rcpt_domains = top(f"SELECT {_domain_sql('recipient')} AS name, COUNT(*) AS messages, SUM(status='bounced') AS bounced {base} AND status <> 'delivered' GROUP BY name ORDER BY messages DESC LIMIT ?")
    top_sender_domains = top(f"SELECT {_domain_sql('sender')} AS name, COUNT(DISTINCT message_id) AS messages {base} AND sender LIKE '%@%' GROUP BY name ORDER BY messages DESC LIMIT ?")

    cats = {}
    examples = {}
    for r in conn.execute(f"SELECT status, reason, recipient {base} AND status IN ('bounced','deferred')", (since,)):
        c = categorize_reason(r["reason"])
        e = cats.setdefault(c, {"category": c, "bounced": 0, "deferred": 0})
        e[r["status"]] += 1
        examples.setdefault(c, (r["reason"] or "")[:160])
    failure_categories = sorted(cats.values(), key=lambda x: -(x["bounced"] + x["deferred"]))
    for c in failure_categories:
        c["example"] = examples.get(c["category"], "")

    att = conn.execute("""
        SELECT COUNT(*) AS messages, SUM(mm.att_count) AS files, SUM(mm.att_total) AS bytes, MAX(mm.att_total) AS max_bytes
        FROM message_meta mm WHERE mm.att_count > 0 AND mm.message_id IN (
            SELECT message_id FROM emails WHERE timestamp >= ?)""", (since,)).fetchone()
    types = {}
    for r in conn.execute("""SELECT mm.attachments FROM message_meta mm WHERE mm.att_count > 0 AND mm.message_id IN (
                               SELECT message_id FROM emails WHERE timestamp >= ?)""", (since,)):
        try:
            for a in json.loads(r["attachments"] or "[]"):
                name = (a.get("name") or "").lower()
                ext = name.rsplit(".", 1)[1] if "." in name and len(name.rsplit(".", 1)[1]) <= 6 else (a.get("type") or "?").split("/")[-1][:12]
                t = types.setdefault(ext, {"ext": ext, "files": 0, "bytes": 0})
                t["files"] += 1
                t["bytes"] += a.get("size") or 0
        except ValueError:
            pass
    conn.close()
    return {"days": days, "since": since, "kpi": kpi, "trend": traffic_by_day(days), "hourly": hourly,
            "top_senders": top_senders, "top_recipients": top_recipients,
            "top_recipient_domains": top_rcpt_domains, "top_sender_domains": top_sender_domains,
            "failure_categories": failure_categories,
            "attachments": dict(att) if att else {}, "attachment_types": sorted(types.values(), key=lambda x: -x["bytes"])[:10]}


# ── Alerts ────────────────────────────────────────────────────────────────────

def compute_alerts(thresholds=None, sync_status=None, message_limit=None):
    t = {"sasl_fail_ip_24h": 10, "sender_msgs_1h": 100, "bounce_ratio_today": 3.0, "deferred_now": 20,
         "sync_stale_min": 10}
    t.update(thresholds or {})
    conn = get_db()
    alerts = []

    def add(sev, code, title, detail, count=None):
        alerts.append({"severity": sev, "code": code, "title": title, "detail": detail, "count": count})

    inj24 = conn.execute("SELECT COUNT(*), COUNT(DISTINCT ip), MAX(ts) FROM security_events WHERE kind='log_injection' AND ts >= datetime('now','localtime','-1 day')").fetchone()
    if inj24[0]:
        add("critical", "log_injection", "Percobaan injeksi log (CVE-2026-73570)",
            f"{inj24[0]} baris dari {inj24[1]} IP dalam 24 jam; terakhir {inj24[2]}. Watcher sudah di-hardening.", inj24[0])
    sasl = conn.execute("""SELECT ip, COUNT(*) AS n, COUNT(DISTINCT detail) AS users, MAX(ts) AS last
                           FROM security_events WHERE kind='sasl_fail' AND ts >= datetime('now','localtime','-1 day')
                           GROUP BY ip HAVING n >= ? ORDER BY n DESC LIMIT 5""", (t["sasl_fail_ip_24h"],)).fetchall()
    for r in sasl:
        add("high", "sasl_bruteforce", f"Brute-force login SMTP dari {r['ip']}",
            f"{r['n']} kegagalan SASL, {r['users']} username berbeda dalam 24 jam; terakhir {r['last']}.", r["n"])

    hv = conn.execute(f"""SELECT sender, COUNT(DISTINCT message_id) AS n FROM emails
                          WHERE timestamp >= datetime('now','localtime','-1 hour') AND NOT {INTERNAL_HOP_SQL} AND sender <> ''
                          GROUP BY sender HAVING n >= ? ORDER BY n DESC LIMIT 5""", (t["sender_msgs_1h"],)).fetchall()
    for r in hv:
        add("high", "high_volume_sender", f"Volume kirim tinggi: {r['sender']}",
            f"{r['n']} pesan dalam 1 jam terakhir (ambang {t['sender_msgs_1h']}). Periksa kemungkinan akun disusupi.", r["n"])

    today = conn.execute(f"SELECT SUM(status='bounced') FROM emails WHERE date(timestamp)=date('now','localtime') AND NOT {INTERNAL_HOP_SQL}").fetchone()[0] or 0
    avg = conn.execute(f"""SELECT AVG(n) FROM (SELECT date(timestamp) d, SUM(status='bounced') n FROM emails
                           WHERE timestamp >= date('now','localtime','-7 days') AND timestamp < date('now','localtime')
                           AND NOT {INTERNAL_HOP_SQL} GROUP BY d)""").fetchone()[0] or 0
    if today >= 5 and today >= t["bounce_ratio_today"] * max(avg, 1):
        add("medium", "bounce_spike", "Lonjakan bounce hari ini",
            f"{today} bounce hari ini vs rata-rata {avg:.1f}/hari (7 hari).", today)

    deferred = conn.execute(f"""SELECT COUNT(*) FROM emails e WHERE e.status='deferred' AND e.timestamp >= datetime('now','localtime','-1 day')
                                AND NOT EXISTS (SELECT 1 FROM emails x WHERE x.queue_id=e.queue_id AND x.recipient=e.recipient
                                                AND x.status IN ('sent','delivered','bounced'))""").fetchone()[0]
    if deferred >= t["deferred_now"]:
        add("medium", "deferred_backlog", "Antrian deferred menumpuk",
            f"{deferred} pengiriman deferred (24 jam) belum terkirim/bounce.", deferred)
    elif deferred:
        add("info", "deferred_pending", "Pengiriman tertunda", f"{deferred} pengiriman deferred (24 jam) masih menunggu.", deferred)

    size_rej = conn.execute("""SELECT COUNT(*) FROM emails WHERE timestamp >= datetime('now','localtime','-7 days')
        AND status IN ('bounced','deferred') AND (reason LIKE '%5.3.4%' OR reason LIKE '%exceeds size limit%' OR reason LIKE '%too large%')""").fetchone()[0]
    if size_rej:
        add("medium", "size_rejects", "Pesan ditolak karena ukuran", f"{size_rej} dalam 7 hari. Pertimbangkan batas ukuran.", size_rej)
    if message_limit:
        near = conn.execute(f"""SELECT COUNT(DISTINCT message_id) FROM emails WHERE timestamp >= datetime('now','localtime','-7 days')
                                AND size >= ? AND NOT {INTERNAL_HOP_SQL}""", (int(message_limit * 0.9),)).fetchone()[0]
        if near:
            add("info", "near_size_limit", "Pesan mendekati batas ukuran", f"{near} pesan ≥ 90% batas dalam 7 hari.", near)

    if sync_status:
        last = sync_status.get("last_sync")
        if last and last != "Never":
            from datetime import datetime as _dt
            try:
                age = (_dt.now() - _dt.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
                if age > t["sync_stale_min"]:
                    add("high", "sync_stale", "Sinkronisasi log terhenti", f"Sync terakhir {last} ({age:.0f} menit lalu).")
            except ValueError:
                pass
        if sync_status.get("last_error"):
            add("medium", "sync_error", "Error sinkronisasi log", sync_status["last_error"][:300])

    recent_inj = [dict(r) for r in conn.execute("""SELECT ts, ip, detail FROM security_events WHERE kind='log_injection'
                                                    ORDER BY ts DESC LIMIT 15""")]
    top_ips = [dict(r) for r in conn.execute("""SELECT ip, SUM(kind='sasl_fail') AS sasl_fail, SUM(kind='log_injection') AS injection,
                                                  COUNT(*) AS total, MAX(ts) AS last FROM security_events
                                                  WHERE ts >= datetime('now','localtime','-7 days') AND ip <> ''
                                                  GROUP BY ip ORDER BY total DESC LIMIT 15""")]
    daily_events = [dict(r) for r in conn.execute("""SELECT date(ts) AS date, SUM(kind='sasl_fail') AS sasl_fail,
                                                       SUM(kind='log_injection') AS injection FROM security_events
                                                       WHERE ts >= date('now','localtime','-13 days') GROUP BY date ORDER BY date""")]
    recent_failures = [dict(r) for r in conn.execute(f"""SELECT timestamp, sender, recipient, status, reason FROM emails
                          WHERE status IN ('bounced','deferred') AND NOT {INTERNAL_HOP_SQL} ORDER BY timestamp DESC LIMIT 15""")]
    for r in recent_failures:
        r["category"] = categorize_reason(r["reason"])
    conn.close()
    order = {"critical": 0, "high": 1, "medium": 2, "info": 3}
    alerts.sort(key=lambda a: order.get(a["severity"], 9))
    return {"alerts": alerts, "thresholds": t, "recent_injections": recent_inj, "top_ips": top_ips,
            "daily_events": daily_events, "recent_failures": recent_failures}
