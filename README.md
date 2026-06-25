# JPAS Financial Analyzer

Dashboard analitik keuangan untuk JPAS (Asuransi Syariah Askrindo).  
Stack: **FastAPI** (Python) backend + **vanilla HTML/JS** frontend.

---

## Cara Install & Menjalankan

### 1. Prerequisites
- Python 3.10+
- pip

### 2. Install dependencies
```bash
cd analyzer/
pip install -r requirements.txt
```

### 3. Konfigurasi environment
```bash
cp .env.example .env
# Edit .env sesuai kebutuhan (ALLOWED_ORIGINS, dll.)
```

### 4. Jalankan server
```bash
python server.py
```
Server berjalan di **http://localhost:8501** secara default.

Atau dengan uvicorn langsung:
```bash
uvicorn server:app --host 0.0.0.0 --port 8501 --reload
```

---

## Cara Penggunaan

1. Buka browser ke `http://localhost:8501`
2. Klik **"Upload Excel"** dan pilih file laporan keuangan JPAS
3. Untuk upload beberapa file sekaligus, gunakan tombol **"Upload Multiple"**
4. Dashboard akan otomatis menghitung rasio keuangan, KPIs, dan AI takeaways

**Format file yang didukung:** `.xlsx` — lihat `EXCEL_FORMAT_GUIDE.md` untuk struktur sheet yang diharapkan.

---

## Environment Variables

| Variable | Default | Keterangan |
|---|---|---|
| `ALLOWED_ORIGINS` | `http://localhost:8501` | CORS origins yang diizinkan, pisahkan dengan koma |
| `JPAS_ACCESS_TOKEN` | _(kosong)_ | Token opsional untuk auth layer di reverse proxy |

---

## API Endpoints

| Method | Path | Keterangan |
|---|---|---|
| `GET` | `/api/health` | Health check — tidak butuh auth, cocok untuk monitoring |
| `GET` | `/api/status` | Status server & daftar file yang sudah diproses |
| `POST` | `/api/upload` | Upload satu file Excel |
| `POST` | `/api/upload-multiple` | Upload beberapa file Excel sekaligus (auto-merge) |
| `GET` | `/api/dashboard` | Data lengkap dashboard (KPIs, tabel, AI takeaways) |
| `GET` | `/api/export` | Export data konsolidasi ke Excel |
| `POST` | `/api/reset` | Reset state server (hapus semua data in-memory) |

---

## Arsitektur Singkat

```
Browser ──► FastAPI (server.py)
                ├── parser.py       # Parse sheet P&L / Neraca / Cash Flow dari Excel
                ├── analyzer.py     # Hitung rasio keuangan (Loss Ratio, ROA, BOPO, dll.)
                ├── reasoning.py    # Generate AI takeaways & critique
                └── utils.py        # Helper functions
```

**Tidak ada database.** Semua data disimpan in-memory — state akan kosong kembali jika server di-restart. Upload ulang file Excel untuk me-reload data.

---

## Yang Perlu Ditambahkan oleh Tim IT

- [ ] **HTTPS / TLS** — wajib sebelum deploy production (gunakan nginx/caddy sebagai reverse proxy)
- [ ] **Login / Auth page** — server ini belum memiliki autentikasi user. Tambahkan di layer reverse proxy atau integrasikan dengan SSO internal.
- [ ] **Persistent storage** — jika dibutuhkan, state bisa di-persist ke Redis atau database agar tidak hilang saat restart.
- [ ] **Process manager** — gunakan `systemd`, `supervisord`, atau Docker agar server otomatis restart jika crash.

### Contoh konfigurasi nginx (minimal)
```nginx
server {
    listen 443 ssl;
    server_name jpas.internal.example.com;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## Kontak

Repository: https://github.com/jevan-ihsan/jpas-tracker-manbis
