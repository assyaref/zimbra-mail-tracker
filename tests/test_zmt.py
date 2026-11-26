import os, sys, tempfile, sqlite3, importlib
STAGE = sys.argv[1]
sys.path.insert(0, STAGE)
tmpd = tempfile.mkdtemp()
os.environ["ZMT_DB"] = os.path.join(tmpd, "t.db")
import parser as P, db
db.init_db()
ok = 0; fail = 0
def check(name, cond):
    global ok, fail
    if cond: ok += 1; print("PASS", name)
    else: fail += 1; print("FAIL", name)

# Sample: ISO (mail.log style) authenticated submission -> amavis -> reinjection -> lmtp + external smtp
ISO = """2026-11-26T10:00:00.100000+07:00 mail postfix/submission/smtpd[11]: AAA111: client=unknown[1.2.3.4], sasl_method=LOGIN, sasl_username=alice@ex.com
2026-11-26T10:00:00.200000+07:00 mail postfix/cleanup[12]: AAA111: message-id=<m1@ex.com>
2026-11-26T10:00:00.300000+07:00 mail postfix/qmgr[13]: AAA111: from=<alice@ex.com>, size=1234, nrcpt=2 (queue active)
2026-11-26T10:00:01.000000+07:00 mail postfix/smtp[14]: AAA111: to=<bob@ex.com>, relay=127.0.0.1[127.0.0.1]:10032, delay=1, status=sent (250 2.0.0 Ok: queued as BBB222)
2026-11-26T10:00:01.100000+07:00 mail postfix/cleanup[12]: BBB222: message-id=<m1@ex.com>
2026-11-26T10:00:01.200000+07:00 mail postfix/qmgr[13]: BBB222: from=<alice@ex.com>, size=1300, nrcpt=2 (queue active)
2026-11-26T10:00:02.000000+07:00 mail postfix/lmtp[15]: BBB222: to=<bob@ex.com>, relay=mail.ex.com[10.0.0.1]:7025, delay=1, status=sent (250 2.1.5 Delivery OK)
2026-11-26T10:00:02.100000+07:00 mail postfix/smtp[16]: BBB222: to=<carol@other.org>, relay=mx.other.org[5.6.7.8]:25, delay=1, status=sent (250 ok)
2026-11-26T10:00:03.000000+07:00 mail postfix/smtp[16]: CCC333: to=<dave@other.org>, relay=none, delay=1, status=deferred (connect to mx: Connection timed out)
2026-11-26T10:00:04.000000+07:00 mail postfix/smtp[16]: DDD444: to=<erin@other.org>, relay=mx[1.1.1.1]:25, delay=1, status=bounced (host said: 550 5.1.1 User unknown)
"""
SYSLOG = """Nov 26 10:00:00 mail amavis[99]: (99-01) Passed CLEAN {RelayedOutbound}, ORIGINATING LOCAL [1.2.3.4]:1 [1.2.3.4] <alice@ex.com> -> <bob@ex.com>, Queue-ID: AAA111, Message-ID: <m1@ex.com>, mail_id: x, Hits: 2.5, size: 1234, queued_as: BBB222, Subject: "Laporan \\"Q4\\" – ümlaut", 1 ms
Nov 26 10:05:00 mail amavis[99]: (99-02) Passed SPAMMY {Relayed}, <x@spam.biz> -> <bob@ex.com>, Queue-ID: SSS999, Message-ID: <s1@spam.biz>, mail_id: y, Hits: 6.1, size: 10, queued_as: TTT000, Subject: "Buy now", 1 ms
Nov 26 10:05:01 mail postfix/qmgr[13]: TTT000: from=<x@spam.biz>, size=10, nrcpt=1 (queue active)
Nov 26 10:05:02 mail postfix/lmtp[15]: TTT000: to=<bob@ex.com>, relay=mail.ex.com[10.0.0.1]:7025, delay=1, status=sent (250 2.1.5 Delivery OK)
"""
# 1) ISO parse
recs = P.parse_log_content(ISO)
by = {(r["queue_id"], r["to"], r["status"]): r for r in recs}
check("iso: 5 recipient records", len(recs) == 5)
r = by.get(("BBB222", "bob@ex.com", "delivered"))
check("iso: lmtp delivered", r is not None)
check("iso: msgid correlated on reinjected queue", r and r["message_id"] == "m1@ex.com")
check("iso: sasl user propagated to reinjected queue via msgid", r and r["auth_username"] == "alice@ex.com")
check("iso: timestamp parsed", r and r["timestamp"] == "2026-11-26 10:00:01")
check("iso: submission/smtpd sasl captured", by[("AAA111","bob@ex.com","sent")]["auth_username"] == "alice@ex.com")
check("iso: deferred + reason", by[("CCC333","dave@other.org","deferred")]["reason"].startswith("connect to mx"))
check("iso: bounced + reason", "User unknown" in by[("DDD444","erin@other.org","bounced")]["reason"])
check("iso: external smtp sent", by[("BBB222","carol@other.org","sent")]["relay"].startswith("mx.other.org"))

# 2) syslog + amavis subject/verdict, combined stream
recs2 = P.parse_log_content(SYSLOG + ISO)
by2 = {(r["queue_id"], r["to"], r["status"]): r for r in recs2}
check("syslog: subject unescaped & non-ascii", by2[("BBB222","bob@ex.com","delivered")]["subject"] == 'Laporan "Q4" – ümlaut')
check("syslog: CLEAN with Hits>0 is not spam", by2[("BBB222","bob@ex.com","delivered")]["main_class"] == "not-spam")
check("syslog: SPAMMY verdict -> spam on queued_as", by2[("TTT000","bob@ex.com","delivered")]["main_class"] == "spam")
check("syslog: ts year inferred", by2[("TTT000","bob@ex.com","delivered")]["timestamp"].endswith("-11-26 10:05:01"))

# 3) byte offsets with non-ascii + incomplete trailing line
fp = os.path.join(tmpd, "mail.log")
data = (SYSLOG + ISO).encode()
with open(fp, "wb") as f: f.write(data + b"2026-11-26T10:09:00 mail postfix/smtp[1]: EEE555: to=<x@y.z>, rel")
sink = {}
last = None
for b, pos in P.stream_parse_log(fp, meta_sink=sink): last = pos
check("offset: stops before incomplete line (byte exact)", last == len(data))
check("meta_sink has subjects by msgid", sink["subjects"].get("m1@ex.com", "").startswith("Laporan"))
check("meta_sink has unflushed queue meta", "AAA111" in sink["queues"])
# resume from offset after line completes
with open(fp, "ab") as f: f.write(b"ay=mx[1.1.1.1]:25, delay=1, status=sent (250 ok)\n")
got = []
for b, pos in P.stream_parse_log(fp, start_offset=last): got += b
check("offset: resumed line parsed exactly once", len(got) == 1 and got[0]["queue_id"] == "EEE555")

# 4) cross-sync correlation via queue_meta (delivery appears in later sync without from/msgid)
part1 = "\n".join(ISO.splitlines()[:3]) + "\n"
part2 = "2026-11-26T10:00:09 mail postfix/lmtp[15]: AAA111: to=<zed@ex.com>, relay=mail.ex.com[10.0.0.1]:7025, delay=1, status=sent (250 ok)\n"
fp2 = os.path.join(tmpd, "x.log"); open(fp2,"w").write(part1)
s1 = {}; [db.insert_records(b) for b, _ in P.stream_parse_log(fp2, meta_sink=s1)]; db.upsert_queue_meta(s1)
off = os.path.getsize(fp2); open(fp2,"a").write(part2)
s2 = {}; [db.insert_records(b) for b, _ in P.stream_parse_log(fp2, start_offset=off, meta_sink=s2)]; db.upsert_queue_meta(s2)
c = sqlite3.connect(os.environ["ZMT_DB"]); c.row_factory = sqlite3.Row
before = dict(c.execute("select * from emails where queue_id='AAA111' and recipient='zed@ex.com'").fetchone())
res = db.enrich_from_meta()
after = dict(c.execute("select * from emails where queue_id='AAA111' and recipient='zed@ex.com'").fetchone())
check("cross-sync: before enrichment msgid is fallback", before["message_id"] == "AAA111" and before["sender"] == "")
check("cross-sync: enrichment restores msgid/sender/ts", after["message_id"] == "m1@ex.com" and after["sender"] == "alice@ex.com" and after["timestamp"] == "2026-11-26 10:00:00")

# 5) upsert never overwrites existing subject / real message-id
db.insert_records([{"message_id":"m9@x","queue_id":"Q9","to":"a@b","status":"delivered","subject":"Original","from":"s@x"}])
db.insert_records([{"message_id":"","queue_id":"Q9","to":"a@b","status":"delivered","subject":"Other","from":"s@x"}])
row = c.execute("select subject, message_id from emails where queue_id='Q9'").fetchone()
check("upsert: subject not overwritten", row["subject"] == "Original")
check("upsert: real message-id not replaced by queue-id", row["message_id"] == "m9@x")
db.insert_records([{"message_id":"Q8","queue_id":"Q8","to":"a@b","status":"sent","subject":"","from":"s@x"}])
db.insert_records([{"message_id":"real8@x","queue_id":"Q8","to":"a@b","status":"sent","subject":"Late subj","from":"s@x"}])
row = c.execute("select subject, message_id from emails where queue_id='Q8'").fetchone()
check("upsert: empty subject filled, fallback msgid upgraded", row["subject"] == "Late subj" and row["message_id"] == "real8@x")

# 6) offsets: rotation handling
conn = db.get_db()
db.set_offset(conn, "/v/mail.log", 5000, 1.0, 111); conn.commit()
check("offset: same inode resumes", db.get_offset(conn, "/v/mail.log", 111, 9000) == 5000)
check("offset: new inode at same path starts at 0", db.get_offset(conn, "/v/mail.log", 222, 100) == 0)
check("offset: rotated file found by inode", db.get_offset(conn, "/v/mail.log.1", 111, 9000) == 5000)
check("offset: truncated file resets", db.get_offset(conn, "/v/mail.log", 111, 10) == 0)

# 7) query: internal hops hidden by default, LIKE escaping
db.insert_records(recs)
rows, total, stats = db.query_emails(per_page=500)
check("query: internal 127.0.0.1 hops hidden", all(not (r["relay"] or "").startswith("127.") for r in rows) and stats["internal_hops"] >= 1)
rows_all, total_all, _ = db.query_emails(per_page=500, include_internal=True)
check("query: include_internal shows them", total_all > total)
rows_pct, t_pct, _ = db.query_emails(q="%")
check("query: '%' search is literal", t_pct == 0)
p1,_,_ = db.query_emails(per_page=1, page=1); p2,_,_ = db.query_emails(per_page=1, page=2)
check("query: pagination distinct pages", p1 and p2 and p1[0]["id"] != p2[0]["id"])
print(f"\nRESULT: {ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
