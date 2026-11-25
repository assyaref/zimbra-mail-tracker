"""
Konfigurasi Zimbra Mail Tracker
Sesuaikan dengan lingkungan server Anda.
"""

import os

# ── Server ────────────────────────────────────────────────────────────────────
HOST = os.getenv("ZMT_HOST", "0.0.0.0")
PORT = int(os.getenv("ZMT_PORT", 5000))
DEBUG = os.getenv("ZMT_DEBUG", "false").lower() == "true"

# ── Zimbra Log Directory ──────────────────────────────────────────────────────
# Lokasi default log Zimbra di Linux: /var/log/zimbra.log atau /var/log/maillog
LOG_DIR = os.getenv("ZMT_LOG_DIR", "/var/log")
LOG_PATTERN = os.getenv("ZMT_LOG_PATTERN", "maillog")  # maillog / mail.log / zimbra.log

# ── App Info ──────────────────────────────────────────────────────────────────
APP_NAME = "ZIMBRA MAIL TRACKER"
APP_SUBTITLE = "INTELLIGENCE MAIL SURVEILLANCE SYSTEM"
APP_VERSION = "v2.0"
AGENCY_CODE = "ZMT-INTEL"
CLASSIFICATION = "CONFIDENTIAL"
