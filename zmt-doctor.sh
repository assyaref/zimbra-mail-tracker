#!/usr/bin/env bash
# ZMT Doctor: read-only VPS diagnostics, then securely submit a summary to ZMT.
set -Eeuo pipefail

CONF="${ZMT_CONFIG:-/etc/zimbra-mail-tracker/config.json}"
TOKEN_FILE="${ZMT_DOCTOR_TOKEN_FILE:-$(dirname "$CONF")/doctor.token}"
REPORT="$(mktemp)"
trap 'rm -f "$REPORT"' EXIT

if [[ $EUID -ne 0 ]]; then
  echo "[ERROR] Jalankan sebagai root: sudo zmt-doctor"
  exit 1
fi
if [[ ! -r "$TOKEN_FILE" ]]; then
  echo "[ERROR] Token tidak ditemukan: $TOKEN_FILE"
  echo "Pastikan ZMT sudah diperbarui dan service sudah direstart."
  exit 1
fi

PORT="$(python3 - "$CONF" <<'PY'
import json,sys
try:
    cfg=json.load(open(sys.argv[1]))
    print(int(cfg.get("port",9444)))
except Exception:
    print(9444)
PY
)"
TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE")"
[[ -n "$TOKEN" ]] || { echo "[ERROR] Token ZMT kosong."; exit 1; }

python3 - "$REPORT" <<'PY'
import json, os, shutil, socket, sqlite3, subprocess, sys
from datetime import datetime

out_path=sys.argv[1]
checks=[]

def run(args, timeout=15):
    try:
        p=subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         timeout=timeout, check=False)
        return p.returncode, (p.stdout or "").strip()
    except Exception as exc:
        return 127, str(exc)

def add(name, status, detail):
    checks.append({"name":name, "status":status, "detail":str(detail)[:500]})

# Zimbra service inventory
rc, output=run(["bash","-lc","su - zimbra -c 'zmcontrol status'"], timeout=25)
running=[line.strip() for line in output.splitlines() if "Running" in line]
stopped=[line.strip() for line in output.splitlines() if "Stopped" in line]
if stopped:
    add("Zimbra services","FAIL",f"{len(stopped)} service berhenti: " + "; ".join(stopped[:4]))
elif running:
    add("Zimbra services","PASS",f"{len(running)} service terdeteksi Running")
else:
    add("Zimbra services","WARN",output[:300] or "Status Zimbra tidak dapat dibaca")

# Amavis managed by Zimbra, not systemd.
rc, output=run(["bash","-lc","su - zimbra -c 'zmamavisdctl status'"], timeout=20)
if rc == 0 and "amavisd is running" in output.lower():
    add("Amavis","PASS",output.replace("\n"," · ")[:300])
else:
    add("Amavis","FAIL",output[:300] or "Status Amavis tidak tersedia")

# ZMT service
rc, output=run(["systemctl","is-active","zimbra-mail-tracker"])
add("ZMT service","PASS" if output.strip()=="active" else "FAIL",
    "systemd: "+(output.strip() or "unknown"))

# Subject visibility in recent Amavis log lines (only counts, no message content).
log_path="/var/log/zimbra.log"
try:
    with open(log_path,"r",encoding="utf-8",errors="ignore") as f:
        tail=f.readlines()[-20000:]
    amavis=[line for line in tail if "amavis[" in line]
    with_subject=[line for line in amavis if "Subject:" in line]
    if with_subject:
        add("Amavis Subject logging","PASS",f"{len(with_subject)} baris Amavis memuat Subject pada 20.000 baris log terakhir")
    elif amavis:
        add("Amavis Subject logging","WARN","Baris Amavis terbaru belum memuat field Subject")
    else:
        add("Amavis Subject logging","WARN","Belum ada baris Amavis pada potongan log terbaru")
except Exception as exc:
    add("Zimbra log","FAIL",f"Tidak dapat membaca {log_path}: {exc}")

# Amavis source/config presence
for label,path in [
    ("Amavis config","/opt/zimbra/conf/amavisd.conf"),
    ("Amavis template source","/opt/zimbra/conf/amavisd.conf.in"),
]:
    add(label,"PASS" if os.path.isfile(path) else "FAIL",
        "File tersedia" if os.path.isfile(path) else "File tidak ditemukan")

# Persistent ZMT database health
db_path=os.getenv("ZMT_DB","/etc/zimbra-mail-tracker/tracker.db")
try:
    conn=sqlite3.connect(f"file:{db_path}?mode=ro",uri=True,timeout=5)
    total=conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    with_subject=conn.execute("SELECT COUNT(*) FROM emails WHERE TRIM(COALESCE(subject,''))<>''").fetchone()[0]
    blank=total-with_subject
    conn.close()
    add("Tracker database","PASS",f"{total} email tersimpan · {with_subject} memiliki Subject · {blank} kosong")
except Exception as exc:
    add("Tracker database","FAIL",f"Database tidak dapat dibaca: {exc}")

# Disk headroom
try:
    usage=shutil.disk_usage("/")
    pct=round(usage.used/max(1,usage.total)*100)
    status="FAIL" if pct>=95 else "WARN" if pct>=85 else "PASS"
    add("Disk usage",status,f"{pct}% terpakai · {usage.free/(1024**3):.1f} GiB tersedia")
except Exception as exc:
    add("Disk usage","WARN",str(exc))

overall="CRITICAL" if any(c["status"]=="FAIL" for c in checks) else "WARNING" if any(c["status"]=="WARN" for c in checks) else "PASS"
report={
    "hostname":socket.gethostname(),
    "checked_at":datetime.now().astimezone().isoformat(timespec="seconds"),
    "overall":overall,
    "checks":checks,
}
with open(out_path,"w",encoding="utf-8") as f:
    json.dump(report,f,ensure_ascii=False)
print(f"[ZMT DOCTOR] {overall} · {len(checks)} pemeriksaan")
for c in checks:
    print(f"[{c['status']}] {c['name']}: {c['detail']}")
PY

# Local loopback only: the dashboard API stores the report for authenticated viewing.
SCHEME="https"
if [[ -f "$CONF" ]]; then
  read -r CERT KEY < <(python3 - "$CONF" <<'PY'
import json,sys
try:
 c=json.load(open(sys.argv[1])); print(c.get("cert",""),c.get("key",""))
except Exception: print("","")
PY
)
  if [[ -z "${CERT:-}" || -z "${KEY:-}" || ! -f "$CERT" || ! -f "$KEY" ]]; then SCHEME="http"; fi
else
  SCHEME="http"
fi
URL="$SCHEME://127.0.0.1:$PORT/api/doctor/report"
if ! curl --silent --show-error --fail --insecure --connect-timeout 5 \
  -H "X-ZMT-Doctor-Token: $TOKEN" -H "Content-Type: application/json" \
  --data-binary "@$REPORT" "$URL"; then
  echo
  echo "[ERROR] Gagal mengirim laporan ke ZMT ($URL)."
  echo "Pastikan zimbra-mail-tracker aktif dan sudah memakai versi ZMT Doctor."
  exit 1
fi
echo
echo "[OK] Laporan berhasil dikirim. Buka dashboard ZMT → ZMT DOCTOR."
