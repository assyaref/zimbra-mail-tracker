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

# 8) store_meta: To/Cc/attachments from a synthetic blob; message detail kinds
import store_meta, base64 as _b64, json as _json
blobdir = os.path.join(tmpd, "store", "0", "1", "msg", "0"); os.makedirs(blobdir)
payload = os.urandom(12345)
eml = ("Message-ID: <det1@ex.com>\r\nFrom: Alice <alice@ex.com>\r\nTo: Bob <bob@ex.com>, carol@other.org\r\n"
       "Cc: =?UTF-8?B?RMOpbmlz?= <dave@other.org>\r\nSubject: Detail test\r\nMIME-Version: 1.0\r\n"
       "Content-Type: multipart/mixed; boundary=XX\r\n\r\n--XX\r\nContent-Type: text/plain\r\n\r\nhello\r\n"
       "--XX\r\nContent-Type: application/pdf; name=\"laporan.pdf\"\r\nContent-Transfer-Encoding: base64\r\n"
       "Content-Disposition: attachment; filename=\"laporan.pdf\"\r\n\r\n"
       + "\r\n".join(_b64.b64encode(payload).decode()[i:i+76] for i in range(0, 16500, 76)) + "\r\n--XX--\r\n")
open(os.path.join(blobdir, "257-1.msg"), "wb").write(eml.encode())
meta, st = store_meta.scan(0, store=os.path.join(tmpd, "store"))
m = meta.get("det1@ex.com", {})
check("store: To parsed", m.get("to") == ["Bob <bob@ex.com>", "carol@other.org"])
check("store: Cc RFC2047 name decoded", m.get("cc") == ["Dénis <dave@other.org>"])
check("store: attachment name/type", m.get("attachments") and m["attachments"][0]["name"] == "laporan.pdf" and m["attachments"][0]["type"] == "application/pdf")
check("store: base64 attachment size exact", m.get("attachments") and m["attachments"][0]["size"] == len(payload))
check("store: body text part not counted", len(m.get("attachments") or []) == 1)
db.upsert_message_meta(meta)
db.insert_records([
  {"message_id":"det1@ex.com","queue_id":"QD1","to":"bob@ex.com","status":"delivered","from":"alice@ex.com","relay":"mail[1.1.1.1]:7025"},
  {"message_id":"det1@ex.com","queue_id":"QD1","to":"dave@other.org","status":"sent","from":"alice@ex.com","relay":"mx[2.2.2.2]:25"},
  {"message_id":"det1@ex.com","queue_id":"QD1","to":"eve@ex.com","status":"delivered","from":"alice@ex.com","relay":"mail[1.1.1.1]:7025"},
  {"message_id":"det1@ex.com","queue_id":"QD0","to":"bob@ex.com","status":"sent","from":"alice@ex.com","relay":"127.0.0.1[127.0.0.1]:10032"}])
d = db.get_message_detail("det1@ex.com", "QD1")
kinds = {r["recipient"]: r["kind"] for r in d["recipients"]}
check("detail: To/CC/BCC kinds", kinds == {"bob@ex.com": "to", "dave@other.org": "cc", "eve@ex.com": "bcc"})
check("detail: internal hop excluded", len(d["recipients"]) == 3)
check("detail: attachment totals", d["att_count"] == 1 and d["att_total"] == len(payload))
filled_before = c.execute("select subject from emails where queue_id='QD1' and recipient='eve@ex.com'").fetchone()[0]
db.fill_subjects_from_meta()
check("detail: empty subject filled from store header", c.execute("select subject from emails where queue_id='QD1' and recipient='eve@ex.com'").fetchone()[0] == "Detail test" and not filled_before)
d2 = db.get_message_detail("UNKNOWNQ", "UNKNOWNQ")
check("detail: no headers -> headers_available False", d2["headers_available"] is False and d2["recipients"] == [])

# 9) mail_limits parsing + usage stats
import mail_limits
attrs = mail_limits._parse_attrs("prov> \n# name x\nzimbraMtaMaxMessageSize: 26214400\nzimbraMtaBlockedExtension: exe\nzimbraMtaBlockedExtension: bat\nzimbraAttachmentsBlocked: FALSE\n")
check("limits: attrs parsed (multi-value)", attrs.get("zimbraMtaMaxMessageSize") == ["26214400"] and attrs.get("zimbraMtaBlockedExtension") == ["exe", "bat"])
eff = int((26214400 - 16*1024) / mail_limits.BASE64_OVERHEAD)
check("limits: effective attachment ~18.3MB for 25MB", 19_000_000 < eff < 19_200_000)
c.execute("update emails set timestamp=datetime('now','localtime') where queue_id='QD1'"); c.commit()
c.execute("update emails set size=24000000 where queue_id='QD1' and recipient='bob@ex.com'"); c.commit()
u = db.limits_usage(26214400)
check("limits: usage largest attachment found", u["largest_attachments"] and u["largest_attachments"]["att_total"] == len(payload))
check("limits: near-limit message counted", u["near_limit_messages"] == 1)

# 10) limit changes: validation + command building (no zmprov executed)
cur = {"zimbra_mta_max_message_size": 25*1048576, "mail_content_max_size": 25*1048576,
       "file_upload_max_size": 10*1048576, "file_upload_max_size_per_file": 0}
def rej(req):
    try: mail_limits.validate(req, cur); return False
    except mail_limits.LimitError: return True
check("limits-edit: reject 0 / >100 / decimal / text / empty / bool", all(rej(r) for r in
      [{"message_mb": 0}, {"message_mb": 101}, {"upload_mb": 2.5}, {"upload_mb": "x; rm -rf /"}, {}, {"message_mb": True}]))
check("limits-edit: reject per-file > upload", rej({"per_file_mb": 20, "upload_mb": 10}))
t, w = mail_limits.validate({"message_mb": 30, "upload_mb": 20}, cur); cmds, ch = mail_limits.build_commands(t, cur)
check("limits-edit: raise message also raises content limit", cmds == ["mcf zimbraMtaMaxMessageSize 31457280", "mcf zimbraMailContentMaxSize 31457280", "mcf zimbraFileUploadMaxSize 20971520"])
t, w = mail_limits.validate({"message_mb": 20}, cur); cmds, _ = mail_limits.build_commands(t, cur)
check("limits-edit: lowering message keeps content limit", cmds == ["mcf zimbraMtaMaxMessageSize 20971520"])
t, w = mail_limits.validate({"message_mb": 25, "upload_mb": 10, "per_file_mb": 0}, cur)
check("limits-edit: identical values -> no commands", mail_limits.build_commands(t, cur)[0] == [])
t, w = mail_limits.validate({"upload_mb": 25}, cur)
check("limits-edit: warning when upload exceeds effective attachment", any("melebihi" in x for x in w))
t, w = mail_limits.validate({"per_file_mb": 5}, cur); cmds, _ = mail_limits.build_commands(t, cur)
check("limits-edit: per-file goes to COS default", cmds == ["mc default zimbraFileUploadMaxSizePerFile 5242880"])
db.add_limits_audit("tester", "127.0.0.1", True, [{"attr": "x", "old": 1, "new": 2}], [], ["ok"])
check("limits-edit: audit stored and listed", db.list_limits_audit(5)[0]["changes"][0]["new"] == 2)

# 11) Priority 1: security events, chart, stats, alerts
evlog = os.path.join(tmpd, "ev.log")
open(evlog, "w").write(
  "2026-11-24T07:19:18.3+07:00 mail postfix/submission/smtpd[1]: warning: unknown[203.0.113.9]: SASL LOGIN authentication failed: authentication failure, sasl_username=a@ex.com\n"
  "Nov 26 01:24:00 mail postfix/smtps/smtpd[2]: NOQUEUE: filter: RCPT from x[198.51.100.7]: <t@e>: x; to=<\"x: Service status change: localhost $(id) changed from stopped to running@x>\n"
  "Nov 25 23:17:53 mail zmconfigd[3]: Service status change: mail.ex.com amavis changed from running to stopped\n")
sink = {}
for _ in P.stream_parse_log(evlog, meta_sink=sink): pass
kinds = sorted(e[0] for e in sink["events"])
check("events: SASL failure + injection captured, zmconfigd ignored", kinds == ["log_injection", "sasl_fail"])
inj = [e for e in sink["events"] if e[0] == "log_injection"][0]
check("events: injection IP and payload extracted", inj[2] == "198.51.100.7" and inj[3] == "$(id)")
n1 = db.insert_security_events(sink["events"]); n2 = db.insert_security_events(sink["events"])
check("events: duplicates ignored", n1 == 2 and n2 == 0)
tr = db.traffic_by_day(7)
check("chart: 7 consecutive days incl. today", len(tr) == 7 and tr[-1]["date"] == __import__("datetime").date.today().isoformat())
check("chart: today's deliveries counted, internal hop excluded", tr[-1]["delivered"] >= 1)
check("categorize: bare domain = transport error", db.categorize_reason("example.com").startswith("Error transport"))
check("categorize: remote host name ignored", db.categorize_reason("host mx.spamexperts.com[1.2.3.4] said: 550 Mailbox is full") == "Mailbox penuh")
st = db.stats_overview(30)
check("stats: kpi + hourly(24) + trend(30)", st["kpi"]["deliveries"] >= 1 and len(st["hourly"]) == 24 and len(st["trend"]) == 30)
c.execute("update security_events set ts=datetime('now','localtime')"); c.commit()
al = db.compute_alerts({"sasl_fail_ip_24h": 1})
codes = {a["code"] for a in al["alerts"]}
check("alerts: injection + brute-force raised", {"log_injection", "sasl_bruteforce"} <= codes)
check("alerts: sorted by severity", al["alerts"][0]["severity"] == "critical")
check("alerts: stale sync detected", any(a["code"] == "sync_stale" for a in db.compute_alerts(sync_status={"last_sync": "2020-01-01 00:00:00"})["alerts"]))
# 12) Firewall toggle (no real ufw/fail2ban/systemctl executed)
import firewall as FW
RULES = "Added user rules (see 'ufw status' for running firewall):\nufw allow 22/tcp comment 'SSH'\nufw allow 9444/tcp\nufw allow in on wg0 from 10.20.0.0/24 to any port 7071 proto tcp\nufw allow 80\n"
check("fw: rule guard finds 22, 9444, bare 80", FW.ufw_allows_port(22, RULES) and FW.ufw_allows_port(9444, RULES) and FW.ufw_allows_port(80, RULES))
check("fw: interface-only rule does not count", not FW.ufw_allows_port(7071, RULES) and not FW.ufw_allows_port(2222, RULES))
def fw_rej(fn, *a):
    try: fn(*a); return False
    except FW.FirewallError: return True
check("fw: reject unknown service / bad duration", fw_rej(FW.disable, "iptables", 15) and fw_rej(FW.disable, "ufw", 7)
      and fw_rej(FW.disable, "ufw", 0) and fw_rej(FW.enable, "sshd"))
calls = []
_orig = FW._run
def fake_run(cmd, timeout=30):
    calls.append(cmd)
    if cmd[0] == FW.SYSTEMD_RUN and any(c.startswith("--on-active") for c in cmd): return 1, "", "denied"
    return 0, "", ""
FW._run = fake_run
ok_, log_ = FW.disable("ufw", 15)
check("fw: no safety timer -> service NOT disabled", not ok_ and not any(FW.UFW in c and "disable" in c for c in calls))
calls.clear()
FW._run = lambda cmd, timeout=30: (calls.append(cmd) or (0, RULES if cmd[:3] == [FW.UFW, "show", "added"] else "", ""))
ok_, log_ = FW.disable("fail2ban", 5)
timer = [c for c in calls if any(x.startswith("--on-active") for x in c)]
check("fw: disable creates 5-min re-enable timer before stopping",
      ok_ and timer and "--on-active=300" in timer[0] and timer[0][-3:] == [FW.SYSTEMCTL, "start", "fail2ban"]
      and calls.index(timer[0]) < next(i for i, c in enumerate(calls) if c[-3:] == [FW.SYSTEMCTL, "stop", "fail2ban"]))
check("fw: enable refused when SSH rule missing", fw_rej(FW.enable, "ufw", [22, 2222]))
calls.clear(); ok_, _ = FW.enable("ufw", [22, 9444])
check("fw: enable runs ufw --force enable and cancels timer", ok_ and any(c[-3:] == [FW.UFW, "--force", "enable"] for c in calls)
      and any(c[:2] == [FW.SYSTEMCTL, "stop"] and "zmt-reenable-ufw.timer" in c for c in calls))
FW._run = _orig
db.add_firewall_audit("tester", "127.0.0.1", "ufw", "disable", 15, True, ["x"])
check("fw: audit stored and listed", db.list_firewall_audit(3)[0]["minutes"] == 15)
print(f"\nRESULT: {ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
