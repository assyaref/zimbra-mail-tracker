#!/bin/bash
# Run Zimbra Mail Tracker directly (tanpa systemd)
set -e
echo ""
echo "  ============================================"
echo "   ZIMBRA MAIL TRACKER - INTEL SYSTEM v2.0"
echo "  ============================================"
echo ""
echo "[*] Starting server (zero dependencies)..."
echo "[*] Open browser: http://localhost:${ZMT_PORT:-5000}"
echo "[*] Press Ctrl+C to stop."
echo ""
python3 server.py
