#!/bin/bash
# zmt-update — update / rollback Zimbra Mail Tracker (seperti zimmon-update)
set -e
DIR=/opt/zimbra-mail-tracker
BAK=/opt/zimbra-mail-tracker-backups
CONF=/etc/zimbra-mail-tracker/config.json
SERVICE=zimbra-mail-tracker
DEFAULT_REPO="https://github.com/assyaref/zimbra-mail-tracker.git"

[ "$(id -u)" = 0 ] || { echo "Jalankan sebagai root"; exit 1; }

cur() { cat "$DIR/VERSION" 2>/dev/null || echo "?"; }

backup() {
  mkdir -p "$BAK"
  local f="$BAK/zmt-$(cur)-$(date +%Y%m%d-%H%M%S).tar.gz"
  tar czf "$f" -C /opt zimbra-mail-tracker
  ls -1t "$BAK"/zmt-*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f
  echo "$f"
}

case "${1:-}" in
  --status)
    echo "Version : $(cur)"
    systemctl status "$SERVICE" --no-pager | head -n 5
    echo "Backups :"
    ls -lh "$BAK"/zmt-*.tar.gz 2>/dev/null || echo "  (belum ada backup)"
    ;;
  --rollback)
    latest=$(ls -1t "$BAK"/zmt-*.tar.gz 2>/dev/null | head -n 1)
    [ -n "$latest" ] || { echo "Tidak ada backup untuk rollback."; exit 1; }
    echo "[*] Rollback ke $latest..."
    rm -rf "$DIR"
    tar xzf "$latest" -C /opt
    systemctl restart "$SERVICE"
    echo "[OK] Rollback selesai. Version: $(cur)"
    ;;
  --git|"")
    repo="${2:-$DEFAULT_REPO}"
    echo "[*] Backup versi sekarang ($(cur))..."
    backup >/dev/null
    tmp=$(mktemp -d)
    git clone --depth 1 "$repo" "$tmp"
    cp -r "$tmp"/* "$DIR"/
    rm -rf "$tmp"
    chmod +x "$DIR/update.sh" "$DIR/run.sh" "$DIR/install.sh" "$DIR/zmt-doctor.sh" 2>/dev/null || true
    ln -sf "$DIR/zmt-doctor.sh" /usr/local/bin/zmt-doctor
    systemctl restart "$SERVICE"
    echo "[OK] Updated ke $(cur)!"
    ;;
  *)
    echo "Usage: zmt-update [--git [URL] | --rollback | --status]"
    exit 1
    ;;
esac
