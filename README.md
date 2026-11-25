# 📡 ZIMBRA MAIL TRACKER — INTEL SYSTEM

Aplikasi tracking & analisis log email Zimbra bergaya **FBI/CIA Intelligence Console**.
Berjalan **standalone tanpa dependensi eksternal** (cukup Python 3 stdlib), bisa dipasang sebagai **systemd service** di VPS Zimbra persis seperti **ZIMMON**.

## ✨ Fitur Utama
- **Zero Dependencies** — Tidak butuh `pip install` atau Flask, cukup `python3` bawaan server.
- **Auto-Parse Zimbra Log** — Otomatis membaca `/var/log/zimbra.log`, `/var/log/maillog`, `/var/log/mail.log` (termasuk arsip `.gz`).
- **Status Tracking Lengkap** — `delivered`, `sent`, `bounced`, `deferred`, serta deteksi `spam` / `not-spam` (Amavis).
- **Kolom Lengkap & Sortable** — Message ID, Timestamp, Auth Domain, Auth Username, Identity, Sender, Recipient, Main Class, Subject, Status.
- **Show/Hide Kolom, Search, Filter, Detail Modal, Grafik 7 Hari, Export CSV, & Upload Log Manual**.
- **HTTPS & Auth Opsional** — Installer otomatis membuat sertifikat TLS self-signed dan Basic Auth opsional.

## 🚀 Cara Install di VPS Zimbra (seperti ZIMMON)

```bash
git clone https://github.com/assyaref/zimbra-mail-tracker.git
cd zimbra-mail-tracker
sudo bash install.sh
```

Setelah selesai, buka di browser:
```
https://<IP-atau-hostname-zimbra>:9444
```

### Perintah Manajemen
```bash
systemctl status zimbra-mail-tracker     # Cek status service
systemctl restart zimbra-mail-tracker    # Restart service
zmt-update --git                         # Update dari GitHub
zmt-update --status                      # Cek versi & backup
zmt-update --rollback                    # Kembalikan versi sebelumnya
```

Konfigurasi tersimpan di `/etc/zimbra-mail-tracker/config.json`.

## 💻 Menjalankan Manual (Windows / Lokal)
```bash
python3 server.py
# atau di Windows: double-click run.bat
# Buka: http://localhost:5000
```
