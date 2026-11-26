#!/usr/bin/env python3
"""Classify Subject candidates (previous zmt.db recovery + current tracker.db) against
Message-ID-verified sources. Read-only on all DBs. Writes a CSV + JSON summary."""
import sys, json, csv, sqlite3, argparse
ap = argparse.ArgumentParser()
ap.add_argument("--tracker", required=True); ap.add_argument("--pre", required=True)
ap.add_argument("--zmt", required=True); ap.add_argument("--store", required=True)
ap.add_argument("--csv", required=True)
a = ap.parse_args()
ro = lambda p: sqlite3.connect(f"file:{p}?mode=ro", uri=True)
T, PRE, Z = ro(a.tracker), ro(a.pre), ro(a.zmt)
for c in (T, PRE, Z): c.row_factory = sqlite3.Row
store = json.load(open(a.store))["consistent"]
norm = lambda s: " ".join((s or "").split())
logsubj = {r[0]: r[1] for r in T.execute("select message_id, subject from message_subjects")} if T.execute("select 1 from sqlite_master where name='message_subjects'").fetchone() else {}

def verdict(msgid, subj):
    """compare subject to store header for msgid"""
    if msgid not in store: return "no_store_evidence"
    s = norm(store[msgid]); x = norm(subj)
    if s == x: return "verified"
    if s and x and (s.startswith(x) or x.startswith(s)): return "verified_truncated"
    return "mismatch"

rows_out = []
summary = {}
def bump(group, key): summary.setdefault(group, {}).setdefault(key, 0); summary[group][key] += 1

# --- A. replay previous integration (366 statements) on pre-integration snapshot
pre = PRE.execute("select * from emails").fetchall()
bym = {}; byqr = {}
for e in pre:
    if e["message_id"] and e["message_id"].strip(): bym[e["message_id"]] = e
    byqr[(e["queue_id"], e["recipient"])] = e
cur = {r["id"]: dict(r) for r in T.execute("select * from emails")}
integ_ids = {}
for ze in Z.execute("select * from emails where coalesce(subject,'')<>''"):
    src = ze["subject_source"] or "log"
    te = bym.get(ze["message_id"]) if (ze["message_id"] or "").strip() else None
    if te is None: te = byqr.get((ze["queue_id"], ze["recipient"]))
    # classify each of the 406 zmt candidate rows
    if te is None:
        cls = "not_matched"
    elif (te["subject"] or "").strip():
        same = norm(te["subject"]) == norm(ze["subject"]) or norm(ze["subject"]).startswith(norm(te["subject"]))
        cls = "already_filled_same" if same else "conflict"
    else:
        cls = "updated_by_previous_integration"
        integ_ids.setdefault(te["id"], set()).add(ze["subject"])
    v = verdict(ze["message_id"], ze["subject"])
    conf = "high(log)" if src == "log" else ("medium(0.9 heuristic)" if "recipient" in src else "low(0.7 heuristic)")
    bump("zmt_406_by_class", cls); bump("zmt_406_by_store_check", v); bump("zmt_406_by_confidence", conf)
    rows_out.append(["zmt_candidate", ze["id"], te["id"] if te is not None else "", ze["queue_id"], ze["message_id"],
                     ze["recipient"], src, conf, cls, v, ze["subject"], store.get(ze["message_id"], "")])
summary["previous_integration"] = {"update_statements": sum(1 for r in rows_out if r[8] == "updated_by_previous_integration"),
                                   "distinct_rows_updated": len(integ_ids),
                                   "rows_receiving_multiple_different_subjects": sum(1 for s in integ_ids.values() if len(s) > 1)}
# --- B. the 80 integration-filled rows as they are now
for tid in integ_ids:
    r = cur.get(tid)
    if not r: bump("integration_80_now", "row_missing"); continue
    v = verdict(r["message_id"], r["subject"]); bump("integration_80_now", v)
    rows_out.append(["integration_filled_row", "", tid, r["queue_id"], r["message_id"], r["recipient"],
                     "zmt.db heuristic", "", "current_subject", v, r["subject"], store.get(r["message_id"], "")])
# --- C. all current non-empty subjects in tracker
for r in cur.values():
    if (r["subject"] or "").strip():
        bump("tracker_nonempty_subject_check", verdict(r["message_id"], r["subject"]))
# --- D. recoverable empty subjects
rec = 0
for r in cur.values():
    if (r["subject"] or "").strip(): continue
    mid = r["message_id"]
    if not mid or mid == r["queue_id"]: bump("tracker_empty_subject", "no_real_message_id"); continue
    if mid in store and norm(store[mid]):
        bump("tracker_empty_subject", "recoverable_store_header"); rec += 1
    elif mid in store: bump("tracker_empty_subject", "store_has_empty_subject")
    elif mid in logsubj: bump("tracker_empty_subject", "recoverable_log_only")
    else: bump("tracker_empty_subject", "no_evidence")
with open(a.csv, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["kind","zmt_id","tracker_id","queue_id","message_id","recipient","source","confidence","class","store_check","subject","store_subject"])
    w.writerows(rows_out)
print(json.dumps(summary, indent=2))
