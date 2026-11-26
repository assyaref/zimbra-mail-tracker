"""
Handling recommendations shown under each alert on the Alerts page.

advice_for(code, ctx) -> {"summary", "steps": [{"text", "cmd"?}], "actions": [{"type", "label", "value"?}]}
category_tip(category) -> one-line hint for a delivery-failure category.

Commands are only displayed (with a copy button); the dashboard never runs them.
Values from the database (IP, address) are validated before they are put into a command.
"""

import ipaddress
import re
import socket

_RE_IP = re.compile(r"^[0-9A-Fa-f.:]{3,45}$")
_RE_ADDR = re.compile(r"^[A-Za-z0-9._%+=-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

PQ = "/opt/zimbra/common/sbin/postqueue"


def _ip(v):
    return v if isinstance(v, str) and _RE_IP.match(v) else None


def _own_ips():
    out = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            out.add(info[4][0])
    except OSError:
        pass
    try:
        with open("/proc/net/fib_trie") as f:  # IPv4 addresses of local interfaces
            out.update(re.findall(r"\|-- (\d+\.\d+\.\d+\.\d+)\n\s+/32 host LOCAL", f.read()))
    except OSError:
        pass
    try:
        with open("/proc/net/if_inet6") as f:  # IPv6 addresses of local interfaces
            for line in f:
                h = line.split()[0]
                out.add(str(ipaddress.ip_address(int(h, 16))))
    except (OSError, ValueError, IndexError):
        pass
    return out


def blockable_ip(v, own=None):
    """Public, non-local IP that is safe to suggest blocking (never loopback, LAN or this server)."""
    ip = _ip(v)
    if not ip:
        return None
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if not a.is_global or a.is_multicast:
        return None
    if ip in (own if own is not None else _own_ips()):
        return None
    return ip


def _addr(v):
    return v if isinstance(v, str) and _RE_ADDR.match(v) else None


def _s(text, cmd=None):
    return {"text": text, "cmd": cmd} if cmd else {"text": text}


def advice_for(code, ctx=None):
    ctx = ctx or {}
    fn = _ADVICE.get(code)
    return fn(ctx) if fn else None


def _log_injection(ctx):
    own = _own_ips()
    ips = [i for i in (blockable_ip(x, own) for x in ctx.get("ips", [])) if i][:5]
    steps = [
        _s("Pastikan hardening watcher (swatchrc.in) masih terpasang. Hasil harus lebih dari 0; "
           "upgrade/patch Zimbra bisa menimpa file ini.",
           "grep -c zm_unsafe_args /opt/zimbra/conf/swatchrc.in"),
        _s("Pastikan notifikasi SNMP tetap mati (harus 'no').",
           "su - zimbra -c 'zmlocalconfig snmp_notify'"),
        _s("Rencanakan upgrade ke Zimbra 10.1.21 atau lebih baru (perbaikan CVE-2026-73570), "
           "lalu pasang ulang hardening swatchrc.in. Perlu jadwal maintenance."),
    ]
    if ips:
        steps.append(_s("Blokir IP penyerang 24 jam terakhir di firewall (permanen, bisa dibuka lagi dengan 'ufw delete deny from <IP>').",
                        "\n".join(f"ufw prepend deny from {ip}" for ip in ips)))
    steps += [
        _s("Cari berkas JSP/skrip baru yang mencurigakan (webshell) 7 hari terakhir.",
           "find /opt/zimbra/jetty_base/webapps /opt/zimbra/jetty -name '*.jsp' -mtime -7 2>/dev/null"),
        _s("Jangan hapus log: baris injeksi adalah bukti. Simpan salinan bila perlu untuk laporan insiden."),
    ]
    return {"summary": "Serangan injeksi perintah lewat log (zmlogswatch). Hardening mencegah eksekusi; "
                       "tujuan langkah ini memastikan proteksi tetap aktif dan menutup sumber serangan.",
            "steps": steps,
            "actions": [{"type": "firewall", "label": "Panel firewall"}]}


def _sasl_bruteforce(ctx):
    ip = blockable_ip(ctx.get("ip"))
    users = [u for u in (_addr(x) for x in ctx.get("users", [])) if u][:5]
    steps = []
    if ip:
        steps += [
            _s("Cek apakah IP ini pernah BERHASIL login (ada sasl_username tanpa 'authentication failed'). "
               "Kosong = belum ada yang berhasil.",
               f"grep -h '\\[{ip}\\]' /var/log/zimbra.log | grep sasl_username | grep -v 'authentication failed' | tail"),
            _s("Cek apakah Fail2ban sudah memblokir IP ini.",
               f"fail2ban-client status zimbra-smtp | grep -c '{ip}'"),
            _s("Blokir IP sekarang lewat jail Fail2ban yang ada.",
               f"fail2ban-client set zimbra-smtp banip {ip}"),
        ]
    if users:
        steps.append(_s("Bila ada login berhasil: ganti password akun yang ditarget dan paksa logout sesi aktif. "
                        "Akun yang dicoba: " + ", ".join(users) + "."))
    steps.append(_s("Bila Fail2ban tidak pernah memblokir walau gagal login berulang, periksa filter jail zimbra-smtp "
                    "(regex harus cocok dengan baris 'SASL LOGIN authentication failed').",
                    "fail2ban-client get zimbra-smtp failregex"))
    return {"summary": "Percobaan menebak password SMTP. Pastikan belum ada yang berhasil, lalu blokir sumbernya.",
            "steps": steps,
            "actions": [{"type": "firewall", "label": "Panel firewall"}]}


def _high_volume_sender(ctx):
    s = _addr(ctx.get("sender"))
    steps = [_s("Buka pesan pengirim ini di tracker: cek subject, penerima, dan apakah isinya wajar (newsletter resmi vs spam).")]
    if s:
        steps += [
            _s("Cek dari IP mana akun ini login. IP asing/luar negeri = tanda akun disusupi.",
               f"grep -h 'sasl_username={s}' /var/log/zimbra.log | grep -oE '\\[[0-9a-fA-F.:]+\\]' | sort | uniq -c | sort -rn | head"),
            _s("Lihat antrean keluar dari akun ini.", f"{PQ} -p | grep -c '{s}'"),
            _s("Bila disusupi: kunci akun dulu, ganti password, lalu buka kembali.",
               f"su - zimbra -c 'zmprov ma {s} zimbraAccountStatus locked'"),
        ]
    steps.append(_s("Setelah akun aman, cek reputasi IP server di blacklist (mis. MXToolbox) karena spam keluar bisa membuat server di-blacklist."))
    return {"summary": "Satu akun mengirim jauh di atas normal. Paling sering karena password akun bocor dan dipakai mengirim spam.",
            "steps": steps,
            "actions": [{"type": "search", "label": "Lihat di tracker", "value": s or ""}]}


def _bounce_spike(ctx):
    return {"summary": "Bounce hari ini jauh di atas rata-rata. Lihat kategori penyebabnya dulu.",
            "steps": [
                _s("Lihat bounce hari ini di tracker dan kategori penyebab di halaman Statistik."),
                _s("Banyak 'spam/kebijakan': cek SPF, DKIM, DMARC domain dan reputasi IP server (blacklist).",
                   "dig +short TXT $(hostname -d); dig +short TXT _dmarc.$(hostname -d)"),
                _s("Banyak 'user tidak dikenal' dari satu pengirim: kemungkinan daftar alamat salah atau akun mengirim spam."),
                _s("Banyak 'error transport lokal' / loop: masalah konfigurasi server, cek akun & transport tujuan."),
            ],
            "actions": [{"type": "status", "label": "Lihat bounce", "value": "bounced"},
                        {"type": "view", "label": "Statistik", "value": "stats"}]}


def _deferred(ctx):
    return {"summary": "Pesan tertunda akan dicoba ulang otomatis oleh Postfix (sampai 5 hari). Cari penyebab bila menumpuk.",
            "steps": [
                _s("Lihat ringkasan antrean.", f"{PQ} -p | tail -1"),
                _s("Lihat alasan tertunda di tracker (status Deferred). Penyebab umum: server tujuan down, timeout, DNS."),
                _s("Setelah penyebab diperbaiki, kirim ulang antrean sekarang (aman, tidak menghapus apa pun).",
                   f"su - zimbra -c '{PQ} -f'"),
            ],
            "actions": [{"type": "status", "label": "Lihat deferred", "value": "deferred"}]}


def _size(ctx):
    return {"summary": "Pesan ditolak/mendekati batas ukuran.",
            "steps": [
                _s("Lihat pengirim & ukuran pesan di tracker. Bila hanya sesekali, sarankan pengguna mengirim tautan berbagi file."),
                _s("Bila sering dan memang dibutuhkan, naikkan batas lewat tombol Ubah Batas. "
                   "Ingat banyak server penerima (mis. Gmail) tetap menolak di atas 25 MB."),
            ],
            "actions": [{"type": "limits", "label": "Ubah batas"}]}


def _sync(ctx):
    return {"summary": "Tracker tidak membaca log baru; data di dashboard bisa tertinggal.",
            "steps": [
                _s("Coba Sync Now. Bila tetap gagal, cek log layanan tracker.",
                   "journalctl -u zimbra-mail-tracker -n 50 --no-pager"),
                _s("Pastikan file log ada dan terus bertambah (rotasi log bisa mengganti file).",
                   "ls -l /var/log/mail.log /var/log/zimbra.log"),
                _s("Bila perlu, restart tracker saja (tidak menyentuh Zimbra).",
                   "systemctl restart zimbra-mail-tracker"),
            ],
            "actions": [{"type": "sync", "label": "Sync Now"}]}


_ADVICE = {
    "log_injection": _log_injection,
    "sasl_bruteforce": _sasl_bruteforce,
    "high_volume_sender": _high_volume_sender,
    "bounce_spike": _bounce_spike,
    "deferred_backlog": _deferred,
    "deferred_pending": _deferred,
    "size_rejects": _size,
    "near_size_limit": _size,
    "sync_stale": _sync,
    "sync_error": _sync,
}


_TIPS = [
    ("Error transport", "Konfigurasi lokal: cek akun tujuan (zmprov ga <alamat> zimbraMailTransport) dan transport map Postfix."),
    ("Loop", "Alamat @hostname (mis. zimbra@mail.domain) tidak dikenali sebagai lokal; arahkan ke alamat @domain atau tambahkan alias."),
    ("Mailbox penuh", "Kotak surat penerima penuh. Untuk akun lokal naikkan zimbraMailQuota atau minta pengguna mengosongkan."),
    ("User tidak dikenal", "Alamat salah ketik atau akun sudah tidak ada. Konfirmasi alamat ke pengirim."),
    ("Ukuran pesan", "Lampiran terlalu besar untuk server tujuan. Kirim lewat tautan berbagi file."),
    ("Ditolak sebagai spam", "Cek reputasi IP server (blacklist) dan SPF/DKIM/DMARC domain."),
    ("DNS", "Domain tujuan salah ketik atau DNS-nya bermasalah. Cek dengan: dig MX <domain>."),
    ("Koneksi", "Server tujuan tidak merespons. Postfix mencoba ulang otomatis; tindak lanjut bila bertahan > 1 hari."),
    ("TLS", "Masalah sertifikat/TLS di server tujuan. Biasanya perlu diperbaiki pihak penerima."),
]


def category_tip(category):
    c = category or ""
    for prefix, tip in _TIPS:
        if c.startswith(prefix):
            return tip
    return "Baca alasan lengkap di detail pesan."
