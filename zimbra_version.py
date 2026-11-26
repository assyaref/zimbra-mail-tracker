"""
Zimbra version check for the dashboard (read-only).

Installed: `zmcontrol -v` (as zimbra) + dpkg versions of zimbra-* packages.
Latest:    release tags of the public Zimbra/zm-build GitHub repository (FOSS builds
           are tagged there, e.g. "10.1.21"). Only a plain GET to api.github.com is
           made; nothing about this server is sent. Can be disabled in config.
Also reports whether the local swatchrc.in hardening and snmp_notify=no are still
in place, because a Zimbra upgrade overwrites swatchrc.in.
"""

import json
import re
import subprocess
import time
import urllib.request

TAGS_URL = "https://api.github.com/repos/Zimbra/zm-build/tags?per_page=100&page={page}"
RELEASE_NOTES = "https://wiki.zimbra.com/wiki/Zimbra_Releases/{v}"
SWATCHRC_IN = "/opt/zimbra/conf/swatchrc.in"
RE_VER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "", str(e)


def vtuple(v):
    m = RE_VER.match(v or "")
    return tuple(int(x) for x in m.groups()) if m else None


def parse_zmcontrol(text):
    """'Release 10.1.20.GA.1048.UBUNTU24.64 UBUNTU24_64 FOSS edition.' -> dict"""
    m = re.search(r"Release\s+(\d+\.\d+\.\d+)\.(\w+)\.(\d+)\.(\S+)\s+(\S+)\s+(\w+)\s+edition", text or "")
    if not m:
        return None
    return {"version": m.group(1), "type": m.group(2), "build": m.group(3), "platform": m.group(5),
            "edition": m.group(6), "raw": text.strip()[:200]}


def installed():
    res = {"errors": []}
    rc, out, err = _run(["su", "-", "zimbra", "-c", "zmcontrol -v"])
    info = parse_zmcontrol(out)
    if info:
        res.update(info)
    else:
        res["errors"].append("zmcontrol -v: " + ((err or out).strip()[:200] or f"exit {rc}"))
    rc, out, _ = _run(["dpkg-query", "-W", "-f", "${Package} ${Version}\\n", "zimbra-*"])
    versions = {}
    for line in out.splitlines():
        p = line.split(None, 1)
        if len(p) == 2 and p[1].strip():
            versions[p[0]] = p[1].strip()
    # Zimbra's own packages carry the release version; third-party ones (zimbra-openssl ...) don't.
    core = {k: v for k, v in versions.items() if re.match(r"^\d+\.\d+\.\d+\.GA\.", v)}
    rel = {}
    for k, v in core.items():
        rel.setdefault(".".join(v.split(".")[:3]), []).append(k)
    res["packages_total"] = len(versions)
    res["packages_core"] = len(core)
    res["package_versions"] = {k: sorted(v) for k, v in rel.items()}
    res["mixed"] = len(rel) > 1
    return res


def hardening():
    out = {}
    try:
        with open(SWATCHRC_IN, encoding="utf-8", errors="replace") as f:
            out["swatchrc_hardened"] = "zm_unsafe_args" in f.read()
    except OSError:
        out["swatchrc_hardened"] = None
    rc, o, _ = _run(["su", "-", "zimbra", "-c", "zmlocalconfig -m nokey snmp_notify"])
    out["snmp_notify"] = o.strip() if rc == 0 else None
    return out


def latest_releases(timeout=10, pages=3):
    """Return sorted list of release versions (x.y.z) from GitHub tags, newest last."""
    found = set()
    for page in range(1, pages + 1):
        req = urllib.request.Request(TAGS_URL.format(page=page),
                                     headers={"User-Agent": "zmt-version-check", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read(2_000_000).decode("utf-8"))
        if not isinstance(data, list):
            break
        for t in data:
            name = t.get("name", "") if isinstance(t, dict) else ""
            if vtuple(name):
                found.add(name)
        if len(data) < 100:
            break
    return sorted(found, key=vtuple)


def compare(current, releases):
    """Latest overall, latest on the same major.minor line, and newer releases on that line."""
    cur = vtuple(current)
    if not cur or not releases:
        return {"latest": releases[-1] if releases else None}
    same_line = [v for v in releases if vtuple(v)[:2] == cur[:2]]
    newer = [v for v in same_line if vtuple(v) > cur]
    latest = releases[-1]
    out = {"latest": latest, "latest_in_line": same_line[-1] if same_line else None,
           "newer_in_line": newer, "behind": len(newer),
           "newer_major": latest if vtuple(latest)[:2] > cur[:2] else None,
           "release_notes": [{"version": v, "url": RELEASE_NOTES.format(v=v)} for v in newer]}
    out["status"] = "outdated" if newer else ("unknown" if not same_line else "current")
    return out


def check(online=True):
    t0 = time.time()
    res = {"checked_at": time.strftime("%Y-%m-%d %H:%M:%S"), "installed": installed(), "hardening": hardening(),
           "online": online}
    if online:
        try:
            rel = latest_releases()
            res["remote"] = compare(res["installed"].get("version"), rel)
            res["remote"]["source"] = "github.com/Zimbra/zm-build (tags)"
        except Exception as e:  # network down, rate limited, etc.
            res["remote"] = {"status": "unknown", "error": f"{type(e).__name__}: {str(e)[:150]}"}
    else:
        res["remote"] = {"status": "unknown", "error": "Pengecekan online dimatikan (version_check_online=false)"}
    res["seconds"] = round(time.time() - t0, 1)
    return res
