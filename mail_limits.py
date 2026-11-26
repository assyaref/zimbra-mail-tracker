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
