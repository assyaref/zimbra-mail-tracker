"""
UFW and Fail2ban status / enable / temporary disable for the dashboard.

Safety model
  * Disable is always temporary: a transient systemd timer (independent of this
    process) turns the service back on after 5, 15 or 60 minutes. The tracker's
    own watchdog thread is a second safety net (see revert_due()).
  * UFW is only enabled when its saved rules still allow SSH and the dashboard
    port, so enabling can never lock the admin out.
  * Write actions run through `systemd-run --wait` because the tracker service
    itself has ProtectSystem=full (/etc read-only) and ufw writes /etc/ufw.
"""

import re
import subprocess
import time

UFW = "/usr/sbin/ufw"
F2B = "/usr/bin/fail2ban-client"
SYSTEMCTL = "/bin/systemctl"
SYSTEMD_RUN = "/usr/bin/systemd-run"

SERVICES = ("ufw", "fail2ban")
DURATIONS_MIN = (5, 15, 60)
TIMER_UNIT = {"ufw": "zmt-reenable-ufw", "fail2ban": "zmt-reenable-fail2ban"}
ENABLE_CMD = {"ufw": [UFW, "--force", "enable"], "fail2ban": [SYSTEMCTL, "start", "fail2ban"]}
DISABLE_CMD = {"ufw": [UFW, "disable"], "fail2ban": [SYSTEMCTL, "stop", "fail2ban"]}


class FirewallError(ValueError):
    pass


def _run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "", str(e)


def _run_outside_sandbox(cmd, timeout=60):
    return _run([SYSTEMD_RUN, "--wait", "--pipe", "--collect", "--quiet"] + cmd, timeout=timeout)


# ── Status ────────────────────────────────────────────────────────────────────

def _ufw_status():
    rc, out, err = _run([UFW, "status"])
    if rc != 0:
        return {"available": False, "active": None, "error": (err or out).strip()[:200]}
    active = bool(re.search(r"^Status:\s*active", out, re.M))
    rules = [l for l in out.splitlines() if re.search(r"\bALLOW|DENY|REJECT|LIMIT\b", l) and "(v6)" not in l]
    return {"available": True, "active": active, "rules": len(rules)}


def _f2b_status():
    rc, out, _ = _run([SYSTEMCTL, "is-active", "fail2ban"])
    state = out.strip() or "unknown"
    res = {"available": state != "unknown", "active": state == "active", "state": state, "jails": []}
    if state != "active":
        return res
    rc, out, err = _run([F2B, "status"])
    m = re.search(r"Jail list:\s*(.*)", out)
    for name in [j.strip() for j in (m.group(1).split(",") if m else []) if j.strip()]:
        if not re.fullmatch(r"[\w.-]+", name):
            continue
        _, jo, _ = _run([F2B, "status", name])
        cur = re.search(r"Currently banned:\s*(\d+)", jo)
        tot = re.search(r"Total banned:\s*(\d+)", jo)
        res["jails"].append({"name": name, "banned": int(cur.group(1)) if cur else 0,
                             "total": int(tot.group(1)) if tot else 0})
    return res


def _timer_active(service):
    rc, out, _ = _run([SYSTEMCTL, "is-active", TIMER_UNIT[service] + ".timer"])
    return out.strip() == "active"


def status(revert_at=None):
    """revert_at: {service: epoch} from the tracker's kv store (for the countdown)."""
    revert_at = revert_at or {}
    out = {"ufw": _ufw_status(), "fail2ban": _f2b_status(), "durations": list(DURATIONS_MIN),
           "checked_at": int(time.time())}
    for s in SERVICES:
        at = revert_at.get(s)
        out[s]["reenable_at"] = int(at) if at and not out[s].get("active") else None
        out[s]["timer"] = _timer_active(s) if not out[s].get("active") else False
    return out


# ── Lockout guard ─────────────────────────────────────────────────────────────

def ufw_allows_port(port, rules_text):
    """True if a saved `ufw allow` rule opens this TCP port (any source, or a plain port rule)."""
    for line in rules_text.splitlines():
        line = line.strip()
        if not line.startswith("ufw allow") or " on " in line:
            continue
        if re.search(rf"(?:allow|port)\s+{port}(?:/tcp)?(?:\s|$)", line):
            return True
        if re.search(rf"\bto any port {port}\b", line) and " from " not in line:
            return True
    return False


def check_ufw_rules(required_ports):
    rc, out, err = _run([UFW, "show", "added"])
    if rc != 0:
        raise FirewallError("Aturan UFW tidak bisa dibaca: " + (err or out).strip()[:200])
    missing = [p for p in required_ports if not ufw_allows_port(p, out)]
    if missing:
        raise FirewallError("UFW tidak diaktifkan: aturan untuk port " + ", ".join(map(str, missing)) +
                            " tidak ada, mengaktifkan UFW bisa mengunci akses admin.")


# ── Actions ───────────────────────────────────────────────────────────────────

def _stop_timer(service):
    unit = TIMER_UNIT[service]
    _run([SYSTEMCTL, "stop", unit + ".timer", unit + ".service"])
    _run([SYSTEMCTL, "reset-failed", unit + ".timer", unit + ".service"])


def enable(service, required_ports=()):
    if service not in SERVICES:
        raise FirewallError("layanan tidak dikenal")
    if service == "ufw":
        check_ufw_rules(required_ports)
    rc, out, err = _run_outside_sandbox(ENABLE_CMD[service])
    _stop_timer(service)
    text = (out + err).strip()[:300]
    return rc == 0, [f"{service} enable rc={rc}" + (f": {text}" if text else "")]


def disable(service, minutes):
    if service not in SERVICES:
        raise FirewallError("layanan tidak dikenal")
    if minutes not in DURATIONS_MIN:
        raise FirewallError("durasi harus salah satu dari " + "/".join(map(str, DURATIONS_MIN)) + " menit")
    # Safety timer first: if it can't be created, don't disable anything.
    _stop_timer(service)
    unit = TIMER_UNIT[service]
    rc, out, err = _run([SYSTEMD_RUN, "--unit=" + unit, f"--on-active={minutes * 60}",
                         "--timer-property=AccuracySec=1s", "--collect", "--quiet"] + ENABLE_CMD[service])
    if rc != 0:
        return False, [f"timer auto-enable gagal dibuat rc={rc}: {(out + err).strip()[:200]}",
                       f"{service} TIDAK dimatikan"]
    rc2, out2, err2 = _run_outside_sandbox(DISABLE_CMD[service])
    text = (out2 + err2).strip()[:300]
    log = [f"timer {unit}.timer: aktif lagi dalam {minutes} menit",
           f"{service} disable rc={rc2}" + (f": {text}" if text else "")]
    if rc2 != 0:
        _stop_timer(service)
    return rc2 == 0, log
