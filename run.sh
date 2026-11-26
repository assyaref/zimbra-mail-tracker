#!/bin/bash
# Run ZimGuard directly (tanpa systemd)
set -e
echo ""
echo "  ============================================"
echo "   ZIMGUARD - ZIMBRA SECURITY & MAIL OPERATIONS"
echo "  ============================================"
echo ""
echo "[*] Starting server (zero dependencies)..."
echo "[*] Open browser: http://localhost:${ZMT_PORT:-5000}"
echo "[*] Press Ctrl+C to stop."
echo ""
python3 server.py
