#!/bin/bash
# ============================================
#  ZIMBRA MAIL TRACKER - INTEL SYSTEM v2.0
#  Run script for Linux/macOS
# ============================================
set -e

echo ""
echo "  ============================================"
echo "   ZIMBRA MAIL TRACKER - INTEL SYSTEM v2.0"
echo "  ============================================"
echo ""

# Check python3
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 not found."
    exit 1
fi

# Virtual env
if [ ! -d "venv" ]; then
    echo "[*] Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

echo "[*] Installing dependencies..."
pip install -r requirements.txt -q

echo "[*] Starting Zimbra Mail Tracker..."
echo "[*] Open browser: http://localhost:5000"
echo "[*] Press Ctrl+C to stop."
echo ""

export ZMT_DEBUG=false
python app.py
