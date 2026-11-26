"""
Read-only view of Zimbra message / attachment size limits.

Sources (nothing is modified):
  * Postfix (effective MTA limit): /opt/zimbra/common/sbin/postconf message_size_limit
  * zmprov (as user zimbra, LDAP):  zimbraMtaMaxMessageSize, zimbraFileUploadMaxSize,
    zimbraMailContentMaxSize, zimbraMtaBlockedExtension, zimbraAttachmentsBlocked,
    COS default zimbraFileUploadMaxSizePerFile
"""

import os
import re
import subprocess
from datetime import datetime

POSTCONF = "/opt/zimbra/common/sbin/postconf"
POSTFIX_CONF = "/opt/zimbra/common/conf"

# base64: 4/3 plus CRLF every 76 chars -> 4/3 * 78/76 ~= 1.368
BASE64_OVERHEAD = (4 / 3) * (78 / 76)

_GCF_ATTRS = ["zimbraMtaMaxMessageSize", "zimbraFileUploadMaxSize", "zimbraMailContentMaxSize",
              "zimbraMtaBlockedExtension", "zimbraMtaBlockedExtensionWarnRecipient", "zimbraAttachmentsBlocked"]


def _run(cmd, stdin=None, timeout=90):
    r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def _parse_attrs(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r"^(zimbra\w+):\s*(.*)$", line.strip())
        if m:
            out.setdefault(m.group(1), []).append(m.group(2).strip())
    return out


def _int(v, default=None):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def read_limits():
    res = {"fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "errors": []}

    # Effective MTA limit (what Postfix actually enforces)
    try:
        rc, out, err = _run([POSTCONF, "-c", POSTFIX_CONF, "-h", "message_size_limit"], timeout=15)
        res["postfix_message_size_limit"] = _int(out) if rc == 0 else None
        if rc != 0:
            res["errors"].append("postconf: " + (err.strip()[:200] or f"exit {rc}"))
    except Exception as e:
        res["postfix_message_size_limit"] = None
        res["errors"].append(f"postconf: {e}")

    # zmprov (LDAP, as zimbra). Hostname is resolved by zmhostname inside the zimbra env.
    cmds = "\n".join([f"gcf {a}" for a in _GCF_ATTRS] +
                     ["gc default zimbraFileUploadMaxSizePerFile zimbraAttachmentsBlocked"]) + "\n"
    attrs = {}
    try:
        rc, out, err = _run(["su", "-", "zimbra", "-c", "zmprov -l"], stdin=cmds)
        attrs = _parse_attrs(out)
        if rc != 0 and not attrs:
            res["errors"].append("zmprov: " + (err.strip()[:200] or f"exit {rc}"))
    except Exception as e:
        res["errors"].append(f"zmprov: {e}")

    def first(name):
        v = attrs.get(name)
        return v[0] if v else None

    res["zimbra_mta_max_message_size"] = _int(first("zimbraMtaMaxMessageSize"))
    res["file_upload_max_size"] = _int(first("zimbraFileUploadMaxSize"))
    res["mail_content_max_size"] = _int(first("zimbraMailContentMaxSize"))
    # gc default prints zimbraFileUploadMaxSizePerFile once (COS default)
    res["file_upload_max_size_per_file"] = _int(first("zimbraFileUploadMaxSizePerFile"))
    blocked = attrs.get("zimbraAttachmentsBlocked") or []
    res["attachments_blocked"] = any(str(b).upper() == "TRUE" for b in blocked)
    exts = [e for e in attrs.get("zimbraMtaBlockedExtension", []) if e]
    res["blocked_extensions"] = sorted(set(exts))
    res["blocked_extension_warn_recipient"] = (first("zimbraMtaBlockedExtensionWarnRecipient") or "").upper() == "TRUE"

    mta = res["postfix_message_size_limit"] or res["zimbra_mta_max_message_size"]
    res["effective_message_limit"] = mta
    # Rough headroom for headers / body text (~16 KB) before base64 expansion
    res["effective_attachment_limit"] = int(max(0, (mta - 16 * 1024)) / BASE64_OVERHEAD) if mta else None
    upload = res["file_upload_max_size"]
    res["effective_webmail_attachment_limit"] = min(x for x in (upload, res["effective_attachment_limit"]) if x) \
        if (upload or res["effective_attachment_limit"]) else None
    res["consistent"] = (res["zimbra_mta_max_message_size"] is None or mta is None
                         or res["zimbra_mta_max_message_size"] == mta)
    return res


# ── Perubahan batas (dipanggil dari dashboard, setelah verifikasi password) ────

MB = 1024 * 1024
BOUNDS = {
    "message_mb": (1, 100),    # zimbraMtaMaxMessageSize (global) -> Postfix message_size_limit via zmconfigd
    "upload_mb": (1, 100),     # zimbraFileUploadMaxSize (global)
    "per_file_mb": (0, 100),   # zimbraFileUploadMaxSizePerFile (COS default), 0 = tidak dibatasi terpisah
}


class LimitError(ValueError):
    pass


def validate(req, current):
    """req: dict MB (int). Return dict byte target + daftar peringatan. Raise LimitError."""
    out, warnings = {}, []
    for key, (lo, hi) in BOUNDS.items():
        v = req.get(key)
        if v is None or v == "":
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise LimitError(f"{key}: harus angka")
        try:
            f = float(v)
        except ValueError:
            raise LimitError(f"{key}: harus angka")
        if f != int(f):
            raise LimitError(f"{key}: gunakan bilangan bulat MB")
        n = int(f)
        if n < lo or n > hi:
            raise LimitError(f"{key}: harus antara {lo} dan {hi} MB")
        out[key] = n
    if not out:
        raise LimitError("tidak ada nilai yang diubah")

    msg = out.get("message_mb", (current.get("zimbra_mta_max_message_size") or 0) // MB)
    upl = out.get("upload_mb", (current.get("file_upload_max_size") or 0) // MB)
    pf = out.get("per_file_mb", (current.get("file_upload_max_size_per_file") or 0) // MB)
    if pf and upl and pf > upl:
        raise LimitError("per_file_mb tidak boleh lebih besar dari upload_mb")
    eff_att = int(max(0, msg * MB - 16 * 1024) / BASE64_OVERHEAD) // MB if msg else 0
    if upl and eff_att and upl > eff_att:
        warnings.append(f"Upload webmail {upl} MB melebihi lampiran efektif ≈{eff_att} MB untuk pesan {msg} MB; "
                        f"lampiran sebesar itu akan ditolak MTA.")
    if msg and msg > 50:
        warnings.append("Batas > 50 MB: banyak server penerima (mis. Gmail 25 MB) tetap akan menolak.")
    return out, warnings


def build_commands(target_mb, current):
    """Perintah zmprov (stdin) untuk perubahan yang benar-benar berbeda dari nilai sekarang."""
    cmds, changes = [], []

    def chg(attr, scope_cmd, old, new):
        if old != new:
            cmds.append(f"{scope_cmd} {attr} {new}")
            changes.append({"attr": attr, "scope": scope_cmd.split()[0] if scope_cmd.startswith("mcf") else "cos default",
                            "old": old, "new": new})

    if "message_mb" in target_mb:
        new = target_mb["message_mb"] * MB
        chg("zimbraMtaMaxMessageSize", "mcf", current.get("zimbra_mta_max_message_size"), new)
        # Konten tersimpan tidak boleh lebih kecil dari pesan yang boleh diterima
        content = current.get("mail_content_max_size") or 0
        if content < new:
            chg("zimbraMailContentMaxSize", "mcf", current.get("mail_content_max_size"), new)
    if "upload_mb" in target_mb:
        chg("zimbraFileUploadMaxSize", "mcf", current.get("file_upload_max_size"), target_mb["upload_mb"] * MB)
    if "per_file_mb" in target_mb:
        chg("zimbraFileUploadMaxSizePerFile", "mc default", current.get("file_upload_max_size_per_file"),
            target_mb["per_file_mb"] * MB)
    return cmds, changes


def apply_commands(cmds):
    """Jalankan zmprov -l lalu flush cache mailboxd. Return (ok, log)."""
    log = []
    rc, out, err = _run(["su", "-", "zimbra", "-c", "zmprov -l"], stdin="\n".join(cmds) + "\n", timeout=120)
    text = (out + err).strip()
    log.append(f"zmprov -l rc={rc}" + (f": {text[:500]}" if text and "ERROR" in text.upper() else ""))
    if rc != 0 or "ERROR" in text.upper():
        return False, log
    # Flush cache agar mailboxd langsung memakai nilai baru (tanpa restart). Gagal di sini tidak fatal:
    # cache LDAP juga kedaluwarsa sendiri.
    rc2, out2, err2 = _run(["su", "-", "zimbra", "-c", "zmprov fc -a config; zmprov fc -a cos default"], timeout=120)
    log.append(f"zmprov flushCache rc={rc2}" + (f": {(out2 + err2).strip()[:300]}" if rc2 else ""))
    log.append("Postfix message_size_limit diperbarui otomatis oleh zmconfigd (interval 60 detik, postfix reload).")
    return True, log
