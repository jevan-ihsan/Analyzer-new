import os
import re
import secrets
import tempfile
import time
import logging
import uvicorn
import io
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import pandas as pd
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Import local modules
from parser import parse_excel_file, merge_financial_dicts
from analyzer import calculate_ratios
from reasoning import generate_takeaways_and_critique
from utils import format_id, detect_current_period, MONTHS_ID

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger("jpas")

_ACCESS_TOKEN = os.environ.get("JPAS_ACCESS_TOKEN", "").strip()

# Upload constraints
ALLOWED_UPLOAD_EXTENSIONS = {".xlsx", ".xlsm", ".xls"}
MAX_UPLOAD_MB = float(os.environ.get("MAX_UPLOAD_MB", "20"))
MAX_UPLOAD_BYTES = int(MAX_UPLOAD_MB * 1024 * 1024)
MAX_FILES_PER_UPLOAD = int(os.environ.get("MAX_FILES_PER_UPLOAD", "10"))

def verify_token(request: Request):
    if not _ACCESS_TOKEN:
        return  # token auth disabled — open beta mode
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or not secrets.compare_digest(auth[7:], _ACCESS_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")

def _validate_upload(filename: str, content: bytes):
    """Reject files with disallowed extensions or excessive size before parsing."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Tipe file tidak didukung ({ext or 'tanpa ekstensi'}). Gunakan file Excel (.xlsx/.xlsm/.xls)."
        )
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Ukuran file melebihi batas {MAX_UPLOAD_MB:.0f} MB."
        )

# Session-Isolated State Cache
session_states = {}
MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "200"))
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_HOURS", "24")) * 3600
_UUID_RE = re.compile(r"^[0-9a-fA-F-]{32,36}$")

def get_default_state():
    return {
        'consolidated_data': {
            'pl_data': {},
            'bs_data': {},
            'gr_data': {},
            'mitra_data': [],
            'cfs_data': None,
            'huw_data': None,
            'pl_df': None,
            'bs_df': None
        },
        'processed_files': set(),
        'last_access': time.time()
    }

def _evict_stale_sessions():
    """Drop expired sessions; if still over capacity, drop the least-recently used."""
    now = time.time()
    expired = [sid for sid, st in session_states.items() if now - st.get('last_access', 0) > SESSION_TTL_SECONDS]
    for sid in expired:
        del session_states[sid]
    while len(session_states) > MAX_SESSIONS:
        oldest = min(session_states, key=lambda s: session_states[s].get('last_access', 0))
        del session_states[oldest]

def get_session_state(request: Request) -> dict:
    session_id = getattr(request.state, "session_id", "default")
    if session_id not in session_states:
        _evict_stale_sessions()
        session_states[session_id] = get_default_state()
    state = session_states[session_id]
    state['last_access'] = time.time()
    return state

def _file_priority_score(filename):
    """
    Score a filename for merge ordering priority (higher = process later = wins).
    Files processed LAST win in the merge since larger absolute values prevail.
    Priority: Lapkeu (official) > Realisasi > Draf Final > Draf > Analysis/Enhanced
    """
    fn = filename.lower()
    if 'lapkeu' in fn:
        return 100
    if 'realisasi' in fn:
        return 80
    if 'final' in fn:
        return 60
    if 'draf' in fn or 'draft' in fn:
        return 40
    # Try to extract date from filename (e.g. 20260618 -> 20260618)
    import re
    date_match = re.search(r'(20\d{6})', fn)
    if date_match:
        return int(date_match.group(1)) // 1000  # relative numeric ordering
    return 20  # default (analysis/enhanced xlsx)

def _merge_parsed_into_state(state, parsed_data, filename):
    """Merge parsed data into session state."""
    state['consolidated_data']['pl_data'] = merge_financial_dicts(
        state['consolidated_data']['pl_data'], parsed_data['pl_data'])
    state['consolidated_data']['bs_data'] = merge_financial_dicts(
        state['consolidated_data']['bs_data'], parsed_data['bs_data'])
    # For gearing ratio data: prefer data with larger equity (more complete balance sheet)
    new_gr = parsed_data.get('gr_data', {})
    old_gr = state['consolidated_data']['gr_data']
    if new_gr:
        if not old_gr:
            state['consolidated_data']['gr_data'] = new_gr.copy()
        else:
            # Prefer the source with larger equity (more likely to be the official value)
            new_equity = new_gr.get('equity', 0.0)
            old_equity = old_gr.get('equity', 0.0)
            if new_equity > old_equity:
                state['consolidated_data']['gr_data'] = new_gr.copy()
    if parsed_data['mitra_data']:
        state['consolidated_data']['mitra_data'] = parsed_data['mitra_data']
    if parsed_data['cfs_data'] is not None:
        state['consolidated_data']['cfs_data'] = parsed_data['cfs_data']
    if parsed_data['huw_data'] is not None:
        state['consolidated_data']['huw_data'] = parsed_data['huw_data']
    if parsed_data['pl_df'] is not None:
        state['consolidated_data']['pl_df'] = parsed_data['pl_df']
    if parsed_data['bs_df'] is not None:
        state['consolidated_data']['bs_df'] = parsed_data['bs_df']
    state['processed_files'].add(filename)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("JPAS Financial Analyzer ready. Upload Excel files via the dashboard.")
    if not _ACCESS_TOKEN:
        logger.warning("JPAS_ACCESS_TOKEN is not set — API auth is disabled. Restrict network access or set a token before wider rollout.")
    yield

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="JPAS Financial Analyzer API", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "http://localhost:8501").split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "").lower() in ("1", "true", "yes")

@app.middleware("http")
async def session_middleware(request: Request, call_next):
    # Retrieve session ID from cookies or headers; only accept well-formed IDs
    # so clients can't register arbitrary strings as state-dict keys.
    incoming = request.cookies.get("session_id") or request.headers.get("x-session-id") or ""
    if _UUID_RE.match(incoming):
        session_id = incoming
        is_new = False
    else:
        session_id = str(uuid.uuid4())
        is_new = True

    request.state.session_id = session_id
    response = await call_next(request)

    if is_new:
        response.set_cookie(
            key="session_id",
            value=session_id,
            path="/",
            httponly=True,
            samesite="lax",
            secure=_COOKIE_SECURE,
            max_age=7 * 24 * 3600
        )
    return response

# 'unsafe-inline' is required by the Tailwind CDN runtime config and inline
# onclick handlers in index.html; the CSP still restricts all external origins
# to the two CDNs and Google Fonts.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "base-uri 'self'"
)

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = _CSP
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response

def clean_html(html_str):
    html_str = html_str.replace('\n', ' ')
    html_str = re.sub(r'\s+', ' ', html_str)
    return html_str.strip()



def evaluate_ratio(val, key):
    if val is None or pd.isna(val) or abs(val) < 1e-9:
        return "-"
        
    thresholds = {
        'komposisi_aset_lancar': (50.0, True),
        'current_ratio': (100.0, True),
        'aset_likuid_vs_klaim_dilaporkan': (120.0, True),
        'kas_giro_vs_utang_penjaminan': (20.0, True),
        'investasi_vs_cadangan_klaim': (150.0, True),
        'aset_lancar_vs_beban_klaim': (200.0, True),
        'aset_likuid_vs_klaim_disetujui': (150.0, True),
        'aset_likuid_vs_proyeksi_klaim': (100.0, True),
        'roa_syariah': (1.5, True),
        'roe_syariah': (5.0, True),
        'bopo_syariah': (90.0, False),
        'net_claim_ratio_syariah': (60.0, False),
        'leverage_ratio_ojk': (3.0, False),
        'pertumbuhan_ijk_syariah': (0.0, True)
    }
    if key not in thresholds:
        return "N/A"
    limit, is_greater_than = thresholds[key]
    if is_greater_than:
        passed = (val >= limit)
    else:
        passed = (val <= limit)
    return "✅ MEMENUHI" if passed else "❌ TIDAK MEMENUHI"

def to_beautiful_table(df, align_right_cols=None):
    import html
    html_output = """
    <div class="overflow-x-auto border border-outline-variant rounded-lg shadow-sm" style="margin-bottom: 24px; font-family: 'Inter', sans-serif;">
        <table class="w-full text-left border-collapse bg-surface-container-lowest text-[13.5px]">
            <thead>
                <tr class="bg-surface-container border-b border-outline-variant/60">
    """
    for col in df.columns:
        align = "right" if align_right_cols is not None and col in align_right_cols else "left"
        escaped_col = html.escape(str(col))
        html_output += f'<th class="px-5 py-3 font-semibold text-on-surface text-xs uppercase tracking-wider text-{align}">{escaped_col}</th>'
    html_output += "</tr></thead><tbody>"
    
    for _, row in df.iterrows():
        label = str(row.iloc[0]).upper()
        is_total = any(x in label for x in ["TOTAL", "LABA", "HASIL UNDERWRITING", "ESTIMASI CADANGAN", "JUMLAH", "SUBTOTAL", "KOMPOSIT", "GEARING RATIO", "ROA", "BOPO", "NET INCOME", "TOTAL EKUITAS", "TOTAL ASET", "KAS BERSIH"])
        
        weight = "font-bold" if is_total else "font-normal"
        text_color = "text-on-surface" if is_total else "text-on-surface-variant"
        row_bg = "bg-surface-container-low" if is_total else "bg-surface-container-lowest"
        border_bottom = "border-b-2 border-outline-variant" if is_total else "border-b border-outline-variant/30"
        
        html_output += f'<tr class="{row_bg} {border_bottom} hover:bg-surface-container-high transition-colors">'
        for i, val in enumerate(row):
            str_val = str(val)
            escaped_str_val = html.escape(str_val)
            align = "right" if align_right_cols is not None and df.columns[i] in align_right_cols else "left"
            font_family = "font-body-md"
            
            disp_val = escaped_str_val
            cell_color = text_color
            if align == "right" and ("%" in str_val or "x" in str_val or "Rp" in str_val or val == "0.00" or str_val.replace(',', '').replace('.', '').replace('-', '').isnumeric()):
                if str_val.startswith("-"):
                    cell_color = "text-error font-medium"
                    disp_val = f"↓ {escaped_str_val}"
                elif "YoY" in df.columns[i] and str_val != "0.00" and not str_val.startswith("-"):
                    cell_color = "text-primary font-medium"
                    disp_val = f"↑ {escaped_str_val}"

            html_output += f'<td class="px-5 py-3 {weight} {cell_color} text-{align} {font_family}">{disp_val}</td>'
        html_output += "</tr>"
        
    html_output += "</tbody></table></div>"
    return clean_html(html_output)

def format_md_to_html(md_text):
    html = md_text
    html = re.sub(r'\*\*(.*?)\*\*', r'<strong class="text-on-surface font-semibold">\1</strong>', html)
    html = html.replace('\n', '<br/>')
    return html

@app.get("/api/health")
def health_check():
    return {"status": "ok"}

@app.get("/api/status")
def get_status(request: Request):
    state = get_session_state(request)
    has_data = bool(state['consolidated_data'].get('pl_data') or state['consolidated_data'].get('bs_data'))
    return {
        "has_data": has_data,
        "processed_files": list(state['processed_files'])
    }

@app.post("/api/upload")
@limiter.limit("10/minute")
async def upload_file(request: Request, file: UploadFile = File(...), _=Depends(verify_token)):
    state = get_session_state(request)
    content = await file.read()
    _validate_upload(file.filename, content)
    suffix = os.path.splitext(file.filename)[1].lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        parsed_data = parse_excel_file(tmp_path)
    except Exception as e:
        logger.exception("Failed to parse uploaded file '%s'", file.filename)
        raise HTTPException(status_code=400, detail=f"Gagal memproses file: {str(e)}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        
    if not (parsed_data.get('pl_data') or parsed_data.get('bs_data')):
        raise HTTPException(status_code=400, detail="Tidak dapat menemukan data Laba Rugi atau Neraca yang valid pada file.")
        
    _merge_parsed_into_state(state, parsed_data, file.filename)
    return {"status": "success", "processed_files": list(state['processed_files'])}

@app.post("/api/upload-multiple")
@limiter.limit("10/minute")
async def upload_multiple_files(request: Request, files: list[UploadFile] = File(...), _=Depends(verify_token)):
    state = get_session_state(request)
    """
    Upload and merge multiple Excel files.
    Files are sorted by priority (oldest/draft first, newest/realisasi/lapkeu last)
    so that the most authoritative data wins in the merge.
    """
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(status_code=400, detail=f"Maksimal {MAX_FILES_PER_UPLOAD} file per unggahan.")

    errors = []
    processed = []
    # First pass: validate and write all files to temp paths
    file_queue = []
    for file in files:
        content = await file.read()
        try:
            _validate_upload(file.filename, content)
        except HTTPException as e:
            errors.append(f"{file.filename}: {e.detail}")
            continue
        suffix = os.path.splitext(file.filename)[1].lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        file_queue.append((file.filename, tmp_path))
    # Sort: process draft/older files first, official/newer files last
    file_queue.sort(key=lambda x: _file_priority_score(x[0]))

    # Second pass: parse and merge in sorted order
    for filename, tmp_path in file_queue:
        try:
            parsed_data = parse_excel_file(tmp_path)
            if not (parsed_data.get('pl_data') or parsed_data.get('bs_data')):
                errors.append(f"{filename}: Tidak dapat menemukan data Laba Rugi atau Neraca yang valid.")
                continue
            _merge_parsed_into_state(state, parsed_data, filename)
            processed.append(filename)
        except Exception as e:
            logger.exception("Failed to parse uploaded file '%s'", filename)
            errors.append(f"{filename}: {str(e)}")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            
    return {
        "status": "success",
        "processed_files": list(state['processed_files']),
        "uploaded": processed,
        "errors": errors
    }

@app.get("/api/export")
def export_excel(request: Request, _=Depends(verify_token)):
    output = io.BytesIO()
    state = get_session_state(request)
    parsed = state['consolidated_data']
    period = detect_current_period(parsed.get('pl_data'), parsed.get('bs_data'))

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pl_keys = parsed.get('pl_data', {})
        if pl_keys:
            pl_rows = []
            ind_pl_labels = {
                'ijk_revenue': 'Pendapatan Imbal Jasa Kafalah (IJK) - Bruto',
                'reinsurance_expense': 'Beban Reasuransi',
                'change_unearned_ijk': 'Kenaikan (Penurunan) IJK YBMP',
                'net_underwriting_revenue': 'Pendapatan Underwriting Bersih',
                'gross_claims': 'Ta\'widh Bruto (Beban Klaim)',
                'reinsurance_claims': 'Ta\'widh Reasuransi',
                'change_claims_retention': 'Kenaikan (Penurunan) Estimasi Tawidh Retensi',
                'net_recoveries': 'Pendapatan Recoveries (Subrogasi) - Bersih',
                'commission_expense': 'Beban Komisi',
                'net_underwriting_result': 'Hasil Underwriting Neto',
                'investment_income': 'Hasil Investasi',
                'total_operating_expense': 'Total Beban Usaha (OPEX)',
                'operating_profit': 'Laba Usaha (EBIT)',
                'pretax_profit': 'Laba Sebelum Pajak (EBT)',
                'net_profit': 'Laba Bersih (Net Income)'
            }
            for k, label in ind_pl_labels.items():
                if k in pl_keys:
                    curr_val = pl_keys[k].get('curr_month', 0.0)
                    rkap_val = pl_keys[k].get('rkap_fy', 0.0)
                    achievement = (curr_val / rkap_val * 100.0) if rkap_val > 0 else 0.0
                    pl_rows.append([
                        label,
                        curr_val,
                        pl_keys[k].get('prev_year_yoy', 0.0),
                        achievement
                    ])
            df_pl = pd.DataFrame(pl_rows, columns=['Uraian Keuangan', f"YTD {period['label']}", f"YTD {period['yoy_label']} (YoY)", 'Pencapaian RKAP FY (%)'])
            df_pl.to_excel(writer, sheet_name='Laba Rugi', index=False)
            
        bs_keys = parsed.get('bs_data', {})
        if bs_keys:
            bs_rows = []
            ind_bs_labels = {
                'cash_and_bank': 'Kas dan Setara Kas',
                'sbsn_invest': 'Investasi - Surat Berharga Syariah Negara (SBSN)',
                'deposito_invest': 'Investasi - Deposito Berjangka Mudharabah',
                'reksadana_invest': 'Investasi - Reksadana & Lainnya',
                'ijk_receivable': 'Piutang Imbal Jasa Kafalah (IJK)',
                'claims_receivable': 'Piutang Ta\'widh',
                'reinsurance_assets': 'Aset Reasuransi',
                'fixed_assets': 'Aset Tetap',
                'total_assets': 'TOTAL ASET',
                'unearned_premium_reserve': 'Estimasi Cadangan Premi (Ujrah YBMP)',
                'claims_reserve_retention': 'Estimasi Ta\'widh Retensi Sendiri',
                'deferred_commission_income': 'Pendapatan Komisi Ditangguhkan',
                'total_liabilities': 'TOTAL LIABILITAS',
                'total_equity': 'TOTAL EKUITAS (Modal Sendiri)'
            }
            for k, label in ind_bs_labels.items():
                if k in bs_keys:
                    bs_rows.append([
                        label,
                        bs_keys[k].get('curr_month', 0.0),
                        bs_keys[k].get('prev_year_yoy', 0.0)
                    ])
            df_bs = pd.DataFrame(bs_rows, columns=['Pos Neraca', period['label'], f"YoY {period['yoy_label']}"])
            df_bs.to_excel(writer, sheet_name='Neraca', index=False)
            
        cfs_df = parsed.get('cfs_data')
        if cfs_df is not None:
            cfs_df.to_excel(writer, sheet_name='Arus Kas', index=False)
            
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=JPAS_Consolidated_Analysis.xlsx"}
    )


@app.post("/api/reset")
def reset_session(request: Request, _=Depends(verify_token)):
    session_id = getattr(request.state, "session_id", "default")
    session_states[session_id] = get_default_state()
    return {"status": "success"}

@app.get("/api/dashboard")
def get_dashboard_data(request: Request, _=Depends(verify_token)):
    state = get_session_state(request)
    parsed = state['consolidated_data']
    has_data = bool(parsed.get('pl_data') or parsed.get('bs_data'))
    if not has_data:
        raise HTTPException(status_code=400, detail="Belum ada data keuangan yang dimuat.")
        
    # Calculate ratios and generate reasoning
    ratios = calculate_ratios(parsed)
    analysis = generate_takeaways_and_critique(ratios, parsed)
    period = ratios['period']
    data_warnings = list(ratios.get('data_warnings', []))

    # ------------------ PRE-PROCESS KPI CARDS ------------------
    gr_val = ratios['solvency']['gearing_ratio']
    lr_val = ratios['underwriting']['loss_ratio']
    roa_val = ratios.get('ojk_health', {}).get('roa_pct', 0.0)

    net_profit_ytd = parsed['pl_data'].get('net_profit', {}).get('curr_month', 0.0) / 1_000_000_000.0
    yoy_growth = ratios['profitability']['yoy_profit_growth_pct']

    ijk_bruto_ytd = parsed['pl_data'].get('ijk_revenue', {}).get('curr_month', 0.0) / 1_000_000_000.0
    ijk_yoy_growth = ratios['underwriting'].get('yoy_ijk_bruto_pct', 0.0)
    
    kpis = {
        "gearing": {"val": f"{format_id(gr_val, decimals=1)}x", "status": f"Limit POJK: {format_id(40.0, decimals=1)}x", "type": "neutral"},
        "loss_ratio": {"val": f"{format_id(lr_val, decimals=1)}%", "status": "Di Bawah Batas OJK (<70,0%)" if lr_val <= 70.0 else "Melebihi Batas OJK (>70,0%)", "type": "success" if lr_val <= 70.0 else "error"},
        "roa": {"val": f"{format_id(roa_val, decimals=1)}%", "status": f"{'Sehat (>= 1,5%)' if roa_val >= 1.5 else 'Rendah (< 1,5%)'}", "type": "success" if roa_val >= 1.5 else "error"},
        "net_profit": {"val": f"Rp {format_id(net_profit_ytd, decimals=1)} M", "status": f"{format_id(yoy_growth, decimals=1, prefix='+')}% YoY" if yoy_growth >= 0 else f"{format_id(yoy_growth, decimals=1)}% YoY", "type": "success" if yoy_growth >= 0 else "error"},
        "ijk_bruto": {"val": f"Rp {format_id(ijk_bruto_ytd, decimals=1)} M", "status": f"{format_id(ijk_yoy_growth, decimals=1, prefix='+')}% YoY" if ijk_yoy_growth >= 0 else f"{format_id(ijk_yoy_growth, decimals=1)}% YoY", "type": "success" if ijk_yoy_growth >= 0 else "error"}
    }
    
    # ------------------ PRE-PROCESS CHARTS ------------------
    # Monthly trend chart extrapolation over the detected reporting period
    curr_month_num = period['month']
    trend_labels = [MONTHS_ID[i][:3] for i in range(curr_month_num)]
    trend_values = [0.0] * curr_month_num

    if 'ijk_revenue' in parsed['pl_data'] and curr_month_num >= 2:
        ijk = parsed['pl_data']['ijk_revenue']
        curr_val = ijk.get('curr_month', 0.0) / 1_000_000_000.0
        # MTD values for the current and previous month, if directly available
        monthly_curr = ijk.get(period['label'], 0.0) / 1_000_000_000.0
        monthly_prev = ijk.get(period['prev_label'], 0.0) / 1_000_000_000.0

        if monthly_curr == 0.0:
            prev_ytd = ijk.get('prev_month', 0.0) / 1_000_000_000.0
            monthly_curr = max(curr_val - prev_ytd, 0.0)

        # Reconstruct earlier months as the average of the remaining YTD amount
        earlier_total = max(curr_val - monthly_curr - monthly_prev, 0.0)
        n_earlier = curr_month_num - 2
        earlier_avg = earlier_total / n_earlier if n_earlier > 0 else 0.0
        trend_values = [earlier_avg] * n_earlier + [monthly_prev, monthly_curr]

    # Portfolio breakdown
    bs_keys = parsed.get('bs_data', {})
    sbsn_val = bs_keys.get('sbsn_invest', {}).get('curr_month', 0.0)
    deposito_val = bs_keys.get('deposito_invest', {}).get('curr_month', 0.0)
    reksadana_val = bs_keys.get('reksadana_invest', {}).get('curr_month', 0.0)
    total_invest = sbsn_val + deposito_val + reksadana_val
    if total_invest > 0:
        sbsn_pct = (sbsn_val / total_invest) * 100
        deposito_pct = (deposito_val / total_invest) * 100
        reksadana_pct = (reksadana_val / total_invest) * 100
    else:
        sbsn_pct = deposito_pct = reksadana_pct = 0.0
        data_warnings.append(
            "Rincian portofolio investasi (SBSN/Deposito/Reksadana) tidak ditemukan di Neraca — "
            "grafik alokasi investasi tidak tersedia."
        )

    charts = {
        "trend_labels": trend_labels,
        "trend_data": [round(v, 2) for v in trend_values],
        "portfolio": [round(sbsn_pct, 1), round(deposito_pct, 1), round(reksadana_pct, 1)]
    }
    
    # ------------------ PRE-RENDER TABLES ------------------
    tables_html = {}
    
    # 1. P&L Table
    pl_keys = parsed.get('pl_data', {})
    if pl_keys:
        pl_rows = []
        ind_pl_labels = {
            'ijk_revenue': 'Pendapatan Imbal Jasa Kafalah (IJK) - Bruto',
            'reinsurance_expense': 'Beban Reasuransi',
            'change_unearned_ijk': 'Kenaikan (Penurunan) IJK YBMP',
            'net_underwriting_revenue': 'Pendapatan Underwriting Bersih',
            'gross_claims': 'Ta\'widh Bruto (Beban Klaim)',
            'reinsurance_claims': 'Ta\'widh Reasuransi',
            'change_claims_retention': 'Kenaikan (Penurunan) Estimasi Tawidh Retensi',
            'net_recoveries': 'Pendapatan Recoveries (Subrogasi) - Bersih',
            'commission_expense': 'Beban Komisi',
            'net_underwriting_result': 'Hasil Underwriting Neto',
            'investment_income': 'Hasil Investasi',
            'total_operating_expense': 'Total Beban Usaha (OPEX)',
            'operating_profit': 'Laba Usaha (EBIT)',
            'pretax_profit': 'Laba Sebelum Pajak (EBT)',
            'net_profit': 'Laba Bersih (Net Income)'
        }
        for k, label in ind_pl_labels.items():
            if k in pl_keys:
                curr_val = pl_keys[k].get('curr_month', 0.0)
                rkap_val = pl_keys[k].get('rkap_fy', 0.0)
                achievement = (curr_val / rkap_val * 100.0) if rkap_val > 0 else 0.0
                pl_rows.append([
                    label,
                    format_id(curr_val),
                    format_id(pl_keys[k].get('prev_year_yoy', 0.0)),
                    format_id(achievement, is_pct=True)
                ])
        df_pl_show = pd.DataFrame(pl_rows, columns=['Uraian Keuangan', f"YTD {period['label']}", f"YTD {period['yoy_label']} (YoY)", 'Pencapaian RKAP FY (%)'])
        tables_html['pl'] = to_beautiful_table(df_pl_show, align_right_cols=df_pl_show.columns[1:])
    else:
        tables_html['pl'] = "<p>Data P&L tidak tersedia.</p>"
        
    # 2. Neraca (Balance Sheet) Table
    if bs_keys:
        bs_rows = []
        ind_bs_labels = {
            'cash_and_bank': 'Kas dan Setara Kas',
            'sbsn_invest': 'Investasi - Surat Berharga Syariah Negara (SBSN)',
            'deposito_invest': 'Investasi - Deposito Berjangka Mudharabah',
            'reksadana_invest': 'Investasi - Reksadana & Lainnya',
            'ijk_receivable': 'Piutang Imbal Jasa Kafalah (IJK)',
            'claims_receivable': 'Piutang Ta\'widh',
            'reinsurance_assets': 'Aset Reasuransi',
            'fixed_assets': 'Aset Tetap',
            'total_assets': 'TOTAL ASET',
            'unearned_premium_reserve': 'Estimasi Cadangan Premi (Ujrah YBMP)',
            'claims_reserve_retention': 'Estimasi Ta\'widh Retensi Sendiri',
            'deferred_commission_income': 'Pendapatan Komisi Ditangguhkan',
            'total_liabilities': 'TOTAL LIABILITAS',
            'total_equity': 'TOTAL EKUITAS (Modal Sendiri)'
        }
        for k, label in ind_bs_labels.items():
            if k in bs_keys:
                bs_rows.append([
                    label,
                    format_id(bs_keys[k].get('curr_month', 0.0)),
                    format_id(bs_keys[k].get('prev_year_yoy', 0.0))
                ])
        df_bs_show = pd.DataFrame(bs_rows, columns=['Pos Neraca', period['label'], f"YoY {period['yoy_label']}"])
        tables_html['bs'] = to_beautiful_table(df_bs_show, align_right_cols=df_bs_show.columns[1:])
    else:
        tables_html['bs'] = "<p>Data Neraca tidak tersedia.</p>"
        
    # 3. Cash Flow Table
    cfs_df = parsed.get('cfs_data')
    if cfs_df is not None:
        cfs_display = cfs_df.copy()
        for col in cfs_display.columns[1:]:
            # Check if column is a percentage column
            is_pct_col = '%' in str(col) or 'rasio' in str(col).lower() or 'yoy' in str(col).lower()
            cfs_display[col] = cfs_display[col].apply(lambda x: format_id(x, is_pct=is_pct_col, is_currency=not is_pct_col) if pd.notna(x) else ("0,00%" if is_pct_col else "Rp0,00"))
        tables_html['cfs'] = to_beautiful_table(cfs_display, align_right_cols=cfs_display.columns[1:])
    else:
        tables_html['cfs'] = '<p class="text-on-surface-variant italic">Data Arus Kas tidak tersedia untuk file ini.</p>'
        
    # 4. Underwriting COB Table
    tables_html['cob'] = '<p class="text-on-surface-variant italic">Data Underwriting COB tidak tersedia untuk file ini.</p>'
    
    # 5. Underwriting HUW Detail Table
    huw_df = parsed.get('huw_data')
    if huw_df is not None:
        huw_display = huw_df.copy()
        for col in huw_display.columns[1:]:
            if 'yoy' in str(col).lower() or '%' in str(col).lower() or 'rasio' in str(col).lower():
                huw_display[col] = huw_display[col].apply(lambda x: format_id(x, is_pct=True))
            else:
                huw_display[col] = huw_display[col].apply(lambda x: format_id(x, is_currency=True))
        tables_html['huw'] = to_beautiful_table(huw_display, align_right_cols=huw_display.columns[1:])
    else:
        tables_html['huw'] = '<p class="text-on-surface-variant italic">Data Driver HUW tidak tersedia untuk file ini.</p>'
        
    # 6. OJK Health Score Table
    ojk_health = ratios.get('ojk_health', {})
    skor_akhir = ojk_health.get('composite_score', 0.0)
    health_data = [
        ["1. Rasio Likuiditas", "Aset Lancar / Utang Lancar", "Asumsi*", "Nilai 1 (Asumsi)", "10%"],
        ["2. Gearing Ratio", "Outstanding Neto / Modal Sendiri Bersih", f"{format_id(gr_val, decimals=2)}x", f"Nilai {ojk_health.get('score_gearing', '-')}", "35%"],
        ["3. Rentabilitas (ROA)", "EBT disetahunkan / Rata-rata Aset", f"{format_id(ojk_health.get('roa_pct', 0.0), decimals=2)}%", f"Nilai {ojk_health.get('score_roa', '-')}", "10,5% (dari 35%)"],
        ["4. Rentabilitas (BOPO)", "Beban Op / Pendapatan Op", f"{format_id(ojk_health.get('bopo_pct', 0.0), decimals=1)}%", f"Nilai {ojk_health.get('score_bopo', '-')}", "12,25% (dari 35%)"],
        ["5. Rentabilitas (Klaim Neto)", "Ta'widh Neto / IJK Neto", f"{format_id(ratios['underwriting']['loss_ratio'], decimals=1)}%", f"Nilai {ojk_health.get('score_klaim', '-')}", "12,25% (dari 35%)"],
        ["6. Tata Kelola (GCG)", "Self-Assessment", "Asumsi*", "Nilai 2 (Asumsi)", "20%"],
        ["SKOR KOMPOSIT AKHIR", "Weighted Average Nilai Komponen", f"{format_id(skor_akhir, decimals=2)}", "SANGAT SEHAT (Skala 1,0 - 1,8)" if skor_akhir <= 1.8 else "SEHAT (Skala 1,8 - 2,5)", "100%"]
    ]
    df_health = pd.DataFrame(health_data, columns=["Komponen Kesehatan OJK", "Indikator / Formula", "Hasil JPAS", "Nilai OJK", "Bobot"])
    tables_html['health'] = to_beautiful_table(df_health) + (
        '<p class="text-[11px] text-on-surface-variant italic" style="margin-top:-16px">'
        '* Komponen Likuiditas (Nilai 1) dan GCG (Nilai 2) adalah asumsi tetap, bukan hasil perhitungan dari file yang diunggah. '
        'Skor komposit akhir memuat kedua asumsi tersebut.</p>'
    )
    
    # 7. DuPont Table
    dupont = ratios.get('dupont', {})
    dupont_data = [
        ["1. Tax Burden (Beban Pajak)", "Net Income ÷ EBT", f"{format_id(dupont.get('tax_burden', 0.0), decimals=3)}", f"{format_id(dupont.get('tax_burden', 0.0)*100, decimals=1)}% dari laba EBT tersisa setelah pajak"],
        ["2. Interest Burden (Beban Non-Op)", "EBT ÷ EBIT", f"{format_id(dupont.get('interest_burden', 0.0), decimals=3)}", f"{format_id(dupont.get('interest_burden', 0.0)*100, decimals=1)}% dari EBIT tersisa setelah beban non-operasional"],
        ["3. Margin EBIT (Profitabilitas)", "EBIT ÷ IJK Bruto", f"{format_id(dupont.get('ebit_margin', 0.0), decimals=3)}", f"{format_id(dupont.get('ebit_margin', 0.0)*100, decimals=1)}% dari pendapatan dikonversi menjadi laba usaha"],
        ["4. Asset Turnover (Turnover Aset)", "IJK Bruto ÷ Avg Assets", f"{format_id(dupont.get('asset_turnover', 0.0), is_ratio=True, decimals=3)}", f"Setiap Rp1 Aset menghasilkan Rp{format_id(dupont.get('asset_turnover', 0.0), decimals=3)} pendapatan bruto"],
        ["5. Financial Leverage (Tuas Keuangan)", "Avg Assets ÷ Avg Equity", f"{format_id(dupont.get('leverage', 0.0), is_ratio=True, decimals=3)}", f"Aset dibiayai oleh ekuitas sebesar {format_id(dupont.get('leverage', 0.0), decimals=2)}x lipat"],
        ["RETURN ON EQUITY (ROE) DUPONT", "Tax × Non-Op × EBIT × Asset Turnover × Leverage", f"{format_id(dupont.get('roe_pct', 0.0), is_pct=True, decimals=1)}", "Estimasi margin ROE hasil dekomposisi 5-faktor"]
    ]
    df_dupont = pd.DataFrame(dupont_data, columns=["Faktor DuPont", "Formula", "Nilai Aktual", "Interpretasi Kinerja"])
    tables_html['dupont'] = to_beautiful_table(df_dupont)
    
    # 8. OJK Compliance Table
    ojk_ratios = ratios.get('ojk_padk47', {})
    ojk_table_data = [
        ["Likuiditas", "Komposisi Aset Lancar", "Aset Lancar &divide; Total Aset", f"{format_id(ojk_ratios.get('komposisi_aset_lancar', 0.0), is_pct=True)}", "&ge; 50,00%", evaluate_ratio(ojk_ratios.get('komposisi_aset_lancar', 0.0), 'komposisi_aset_lancar')],
        ["Likuiditas", "Current Ratio", "Aset Lancar &divide; Liabilitas Lancar", f"{format_id(ojk_ratios.get('current_ratio', 0.0), is_pct=True)}", "&ge; 100,00%", evaluate_ratio(ojk_ratios.get('current_ratio', 0.0), 'current_ratio')],
        ["Likuiditas", "Aset Likuid vs Klaim Dilaporkan", "Aset Likuid &divide; Cadangan Klaim", f"{format_id(ojk_ratios.get('aset_likuid_vs_klaim_dilaporkan', 0.0), is_pct=True)}", "&ge; 120,00%", evaluate_ratio(ojk_ratios.get('aset_likuid_vs_klaim_dilaporkan', 0.0), 'aset_likuid_vs_klaim_dilaporkan')],
        ["Likuiditas", "Kas & Giro vs Utang Penjaminan", "Kas & Giro &divide; Utang Penjaminan", f"{format_id(ojk_ratios.get('kas_giro_vs_utang_penjaminan', 0.0), is_pct=True)}", "&ge; 20,00%", evaluate_ratio(ojk_ratios.get('kas_giro_vs_utang_penjaminan', 0.0), 'kas_giro_vs_utang_penjaminan')],
        ["Likuiditas", "Investasi vs Cadangan Klaim", "Total Investasi &divide; Cadangan Klaim", f"{format_id(ojk_ratios.get('investasi_vs_cadangan_klaim', 0.0), is_pct=True)}", "&ge; 150,00%", evaluate_ratio(ojk_ratios.get('investasi_vs_cadangan_klaim', 0.0), 'investasi_vs_cadangan_klaim')],
        ["Likuiditas", "Aset Lancar vs Beban Klaim", "(Aset Lancar - CKPN IJK) &divide; Klaim Neto", f"{format_id(ojk_ratios.get('aset_lancar_vs_beban_klaim', 0.0), is_pct=True)}", "&ge; 200,00%", evaluate_ratio(ojk_ratios.get('aset_lancar_vs_beban_klaim', 0.0), 'aset_lancar_vs_beban_klaim')],
        ["Likuiditas", "Aset Likuid vs Klaim Disetujui", "Aset Likuid &divide; Utang Klaim Lancar", f"{format_id(ojk_ratios.get('aset_likuid_vs_klaim_disetujui', 0.0), is_pct=True)}", "&ge; 150,00%", evaluate_ratio(ojk_ratios.get('aset_likuid_vs_klaim_disetujui', 0.0), 'aset_likuid_vs_klaim_disetujui')],
        ["Likuiditas", "Aset Likuid vs Proyeksi Klaim", "Aset Likuid &divide; (Cadangan Klaim &times; 1.2)", f"{format_id(ojk_ratios.get('aset_likuid_vs_proyeksi_klaim', 0.0), is_pct=True)}", "&ge; 100,00%", evaluate_ratio(ojk_ratios.get('aset_likuid_vs_proyeksi_klaim', 0.0), 'aset_likuid_vs_proyeksi_klaim')],
        ["Rentabilitas", "ROA Syariah", "Laba Setelah Pajak disetahunkan &divide; Rata-rata Aset", f"{format_id(ojk_ratios.get('roa_syariah', 0.0), is_pct=True)}", "&ge; 1,50%", evaluate_ratio(ojk_ratios.get('roa_syariah', 0.0), 'roa_syariah')],
        ["Rentabilitas", "ROE Syariah", "Laba Setelah Pajak disetahunkan &divide; Rata-rata Ekuitas", f"{format_id(ojk_ratios.get('roe_syariah', 0.0), is_pct=True)}", "&ge; 5,00%", evaluate_ratio(ojk_ratios.get('roe_syariah', 0.0), 'roe_syariah')],
        ["Rentabilitas", "BOPO Syariah", "(Klaim Neto + OPEX) &divide; (UW Bersih + Investasi)", f"{format_id(ojk_ratios.get('bopo_syariah', 0.0), is_pct=True)}", "&le; 90,00%", evaluate_ratio(ojk_ratios.get('bopo_syariah', 0.0), 'bopo_syariah')],
        ["Rentabilitas", "Net Claim Ratio Syariah", "Ta'widh Neto &divide; IJK Neto", f"{format_id(ojk_ratios.get('net_claim_ratio_syariah', 0.0), is_pct=True)}", "&le; 60,00%", evaluate_ratio(ojk_ratios.get('net_claim_ratio_syariah', 0.0), 'net_claim_ratio_syariah')],
        ["Solvabilitas", "Leverage Ratio OJK", "Total Liabilitas &divide; Total Ekuitas", f"{format_id(ojk_ratios.get('leverage_ratio_ojk', 0.0), is_ratio=True)}", "&le; 3,00x", evaluate_ratio(ojk_ratios.get('leverage_ratio_ojk', 0.0), 'leverage_ratio_ojk')],
        ["Pertumbuhan", "Pertumbuhan IJK Syariah", "Kenaikan YoY IJK Bruto", f"{format_id(ojk_ratios.get('pertumbuhan_ijk_syariah', 0.0), is_pct=True)}", "&gt; 0,00%", evaluate_ratio(ojk_ratios.get('pertumbuhan_ijk_syariah', 0.0), 'pertumbuhan_ijk_syariah')]
    ]
    df_ojk = pd.DataFrame(ojk_table_data, columns=["Kategori", "Rasio Keuangan", "Indikator / Formula", "Hasil JPAS", "Benchmark OJK", "Status"])
    tables_html['ojk'] = to_beautiful_table(df_ojk)
    
    # ------------------ PRE-PROCESS AI TAKEAWAYS ------------------
    findings = {
        "kritis": [format_md_to_html(t) for t in analysis['kritis']],
        "perhatian": [format_md_to_html(t) for t in analysis['perhatian']],
        "positif": [format_md_to_html(t) for t in analysis['positif']],
        "anomali_positif": [],
        "anomali_peringatan": [],
        "anomali_negatif": []
    }
    
    if 'anomali_dinamis' in analysis and analysis['anomali_dinamis']:
        for t in analysis['anomali_dinamis']:
            tipe = t.get('tipe')
            card_html = format_md_to_html(f"**{t['title']}**\n\n{t['content']}")
            if tipe == 'positif':
                findings['anomali_positif'].append(card_html)
            elif tipe == 'peringatan':
                findings['anomali_peringatan'].append(card_html)
            elif tipe == 'negatif':
                findings['anomali_negatif'].append(card_html)
                
    # Cause-Effect narratives
    cause_effect = {
        "underwriting": format_md_to_html(analysis['critical_analysis']['kinerja_underwriting']),
        "profitability": format_md_to_html(analysis['critical_analysis']['kinerja_profitabilitas']),
        "concentration": format_md_to_html(analysis['critical_analysis']['risiko_konsentrasi']),
        "solvency": format_md_to_html(analysis['critical_analysis']['solvabilitas_regulasi']),
        "dupont": format_md_to_html(analysis['dupont_analysis'])
    }
    
    # Solvency details
    solvency = {
        "os_net": f"Rp {format_id(ratios['solvency']['os_net_juta'] / 1000.0, decimals=2)} M",
        "equity": f"Rp {format_id(ratios['solvency']['equity_juta'] / 1000.0, decimals=2)} M",
        "capacity": f"Rp {format_id(ratios['solvency']['additional_capacity_triliun'], decimals=2)} Triliun",
        "gearing_limit": format_id(ratios['solvency']['limit_gearing'], is_ratio=True, decimals=1),
        "gearing_val": gr_val
    }
    
    investasi = {
        "total": f"Rp {format_id(total_invest / 1_000_000_000.0, decimals=2)} M" if total_invest > 0 else "-",
        "sbsn_pct": round(sbsn_pct, 1),
        "deposito_pct": round(deposito_pct, 1),
        "reksadana_pct": round(reksadana_pct, 1),
        "sbsn_val": f"Rp {format_id(sbsn_val / 1_000_000_000.0, decimals=2)} M" if sbsn_val > 0 else "-",
        "deposito_val": f"Rp {format_id(deposito_val / 1_000_000_000.0, decimals=2)} M" if deposito_val > 0 else "-",
        "reksadana_val": f"Rp {format_id(reksadana_val / 1_000_000_000.0, decimals=2)} M" if reksadana_val > 0 else "-"
    }
    
    return {
        "kpis": kpis,
        "charts": charts,
        "tables": tables_html,
        "findings": findings,
        "cause_effect": cause_effect,
        "solvency": solvency,
        "ratios": ratios,
        "investasi": investasi,
        "period": period,
        "data_warnings": data_warnings
    }

# Mount static frontend directory
app.mount("/", StaticFiles(directory="frontend", html=True), name="static")

if __name__ == "__main__":
    # Bind to localhost by default; set HOST=0.0.0.0 explicitly to expose on the network.
    uvicorn.run(
        app,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8501"))
    )
