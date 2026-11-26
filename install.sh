#!/bin/bash
# ZIMGUARD (zimbra-mail-tracker) installer — jalankan sebagai root di VPS Zimbra
# Pola instalasi sama seperti ZIMMON (/opt/zimbra-mail-tracker + systemd + HTTPS)
set -e
[ "$(id -u)" = 0 ] || { echo "Jalankan sebagai root (sudo bash install.sh)"; exit 1; }

SRC="$(cd "$(dirname "$0")" && pwd)"
DIR=/opt/zimbra-mail-tracker
CONF_DIR=/etc/zimbra-mail-tracker
CONF_FILE="$CONF_DIR/config.json"
SERVICE=zimbra-mail-tracker
PORT=${ZMT_PORT:-9444}
HOST=$(su - zimbra -c "zmhostname" 2>/dev/null || hostname -f)

echo ""
echo "=================================================="
echo "  ZIMGUARD — ZIMBRA SECURITY & MAIL OPERATIONS INSTALLER"
echo "=================================================="
echo ""

# 1. Copy files to /opt/zimbra-mail-tracker
mkdir -p "$DIR" "$CONF_DIR"
cp -r "$SRC"/* "$DIR"/
chmod +x "$DIR/update.sh" "$DIR/run.sh" "$DIR/zmt-doctor.sh" 2>/dev/null || true

# 2. Generate self-signed SSL cert if not exists
CERT="$CONF_DIR/cert.pem"
KEY="$CONF_DIR/key.pem"
if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
  echo "[*] Generating self-signed TLS certificate for $HOST..."
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout "$KEY" -out "$CERT" \
    -subj "/CN=$HOST/O=ZMT-INTEL" 2>/dev/null
  chmod 600 "$KEY"
fi

# 3. Create config.json if not exists
if [ ! -f "$CONF_FILE" ]; then
  echo ""
  read -rp "Username login dashboard (kosongkan untuk tanpa login): " INPUT_USER
  PASS_HASH=""
  if [ -n "$INPUT_USER" ]; then
    read -rsp "Password untuk '$INPUT_USER': " INPUT_PASS
    echo ""
    PASS_HASH=$(python3 -c "import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" "$INPUT_PASS")
  fi

  LOG_DIR="/var/log"
  [ -f /var/log/zimbra.log ] && LOG_DIR="/var/log"

  cat > "$CONF_FILE" <<EOF
{
  "host": "0.0.0.0",
  "port": $PORT,
  "log_dir": "$LOG_DIR",
  "cert": "$CERT",
  "key": "$KEY",
  "username": "$INPUT_USER",
  "password_hash": "$PASS_HASH",
  "auto_load_interval": 60
}
EOF
  chmod 600 "$CONF_FILE"
  echo "[OK] Config saved to $CONF_FILE"
fi

# 4. Install zmt-update command
ln -sf "$DIR/update.sh" /usr/local/bin/zmt-update
ln -sf "$DIR/zmt-doctor.sh" /usr/local/bin/zmt-doctor

# 5. Create systemd service
cat > "/etc/systemd/system/${SERVICE}.service" <<EOF
[Unit]
Description=ZimGuard — Zimbra Security & Mail Operations
After=network.target

[Service]
Type=simple
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 $DIR/server.py
Restart=always
RestartSec=3
Environment=ZMT_CONFIG=$CONF_FILE

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

# 6. Open firewall if ufw or firewalld is active
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow "$PORT/tcp" >/dev/null 2>&1 || true
elif command -v firewall-cmd >/dev/null && systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --add-port="${PORT}/tcp" >/dev/null 2>&1 || true
  firewall-cmd --reload >/dev/null 2>&1 || true
fi

echo ""
echo "=================================================="
echo "  [OK] ZIMGUARD TERPASANG & AKTIF!"
echo "=================================================="
echo "  URL      : https://${HOST}:${PORT}"
echo "  Service  : systemctl status $SERVICE"
echo "  Config   : $CONF_FILE"
echo "  Update   : zmt-update --git"
echo "=================================================="
echo ""
