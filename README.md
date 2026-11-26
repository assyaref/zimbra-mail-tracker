# 🛡️ ZIMGUARD — Zimbra Security & Mail Operations

*Sebelumnya bernama **Zimbra Mail Tracker**. Nama teknis tetap sama: layanan `zimbra-mail-tracker`, folder `/opt/zimbra-mail-tracker`, konfigurasi `/etc/zimbra-mail-tracker`.*

Konsol operasional & keamanan untuk server Zimbra: pelacakan email, statistik, deteksi serangan, kontrol UFW/Fail2ban, batas ukuran pesan, dan cek versi Zimbra. Bergaya **FBI/CIA Intelligence Console**.
Berjalan **standalone tanpa dependensi eksternal** (cukup Python 3 stdlib), bisa dipasang sebagai **systemd service** di VPS Zimbra persis seperti **ZIMMON**.

## ✨ Fitur Utama
- **Statistik & Alerts** — Grafik lalu lintas, ranking pengirim/penerima, kategori kegagalan; deteksi injeksi log, brute-force SMTP, lonjakan bounce, antrean deferred, beserta **rekomendasi penanganan**.
- **Kontrol Keamanan** — Status & enable/disable sementara UFW dan Fail2ban (auto-aktif kembali), dengan password & audit.
- **Manajemen Zimbra** — Ubah batas ukuran pesan/lampiran dan **cek versi Zimbra** vs rilis terbaru.
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
zmt-doctor                                # Diagnostik VPS dan kirim hasil ke dashboard
```

Konfigurasi tersimpan di `/etc/zimbra-mail-tracker/config.json`.

### ZMT Doctor
Jalankan `sudo zmt-doctor` di VPS untuk memeriksa status Zimbra, Amavis, service ZMT, log Subject, database, dan disk. Ringkasan dikirim melalui loopback ke dashboard dan ditampilkan pada panel **ZMT Doctor / System Health**. Script bersifat read-only, tidak mengubah konfigurasi, dan tidak me-restart service. Token agen disimpan otomatis di `/etc/zimbra-mail-tracker/doctor.token` dengan permission root-only.

## 💻 Menjalankan Manual (Windows / Lokal)
```bash
python3 server.py
# atau di Windows: double-click run.bat
# Buka: http://localhost:5000
```
