import logging
import pandas as pd
import re

logger = logging.getLogger("jpas.utils")

MONTHS_ID = ["Januari", "Februari", "Maret", "April", "Mei", "Juni",
             "Juli", "Agustus", "September", "Oktober", "November", "Desember"]

_MONTH_KEY_RE = re.compile(
    r'^(Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember) (20\d{2})$'
)

def detect_current_period(pl_data=None, bs_data=None, default_label="Mei 2026"):
    """
    Detect the current reporting period from month-labeled keys produced by the
    parser (e.g. 'Mei 2026'). Returns a dict with the current, previous-month,
    and same-month-previous-year labels plus the month number used for
    annualization. Falls back to `default_label` when no month keys are found.
    """
    latest = None  # (year, month)
    for data in (pl_data or {}, bs_data or {}):
        for entry in data.values():
            if not isinstance(entry, dict):
                continue
            for key in entry:
                m = _MONTH_KEY_RE.match(str(key))
                if not m:
                    continue
                # 'Desember <year>' keys come from audited prior-year columns
                # (classify_columns), not the current reporting month — skip them.
                if m.group(1) == "Desember":
                    continue
                month_num = MONTHS_ID.index(m.group(1)) + 1
                candidate = (int(m.group(2)), month_num)
                if latest is None or candidate > latest:
                    latest = candidate

    if latest is None:
        m = _MONTH_KEY_RE.match(default_label)
        latest = (int(m.group(2)), MONTHS_ID.index(m.group(1)) + 1)

    year, month = latest
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    return {
        'label': f"{MONTHS_ID[month - 1]} {year}",
        'month': month,
        'year': year,
        'month_name': MONTHS_ID[month - 1],
        'prev_label': f"{MONTHS_ID[prev_month - 1]} {prev_year}",
        'yoy_label': f"{MONTHS_ID[month - 1]} {year - 1}",
    }

def classify_sheet_by_content(df):
    """
    Classify a sheet based on the occurrences of specific row labels or keywords in cells.
    Returns:
        list: list of strings containing matches from ['BS', 'PL', 'Gearing', 'Mitra', 'CF', 'HUW'] or ['unknown']
    """
    # Convert all string cells in the first few columns to normalized lowercase
    text_content = []
    # Scan up to first 4 columns, since description is usually in the first columns
    max_cols = min(df.shape[1], 4)
    for col_idx in range(max_cols):
        col_vals = df.iloc[:, col_idx].dropna().astype(str).str.lower().str.strip()
        text_content.extend(col_vals.tolist())
    
    # Clean up text content to set of strings
    text_set = {re.sub(r'\s+', ' ', s) for s in text_content}
    
    # Keywords definitions — matched as substrings so extra words, prefixes,
    # or numbering in a cell don't break recognition.
    bs_keywords = {
        'kas dan bank', 'kas dan giro bank', 'kas dan setara kas',
        'piutang imbal jasa kafalah', 'piutang premi',
        'piutang tawidh', "piutang ta'widh", 'aset tetap', 'aktiva tetap',
        'jumlah aset', 'total aset', 'jumlah aktiva', 'total aktiva',
        'jumlah liabilitas', 'total liabilitas', 'jumlah kewajiban', 'total kewajiban',
        'jumlah ekuitas', 'total ekuitas', 'jumlah modal', 'saldo laba',
        'cadangan klaim', 'estimasi tawidh retensi sendiri',
        'deposito berjangka mudharabah', 'deposito pada bank', 'reksa dana syariah', 'reksadana'
    }

    pl_keywords = {
        'imbal jasa kafalah bruto', 'beban penjaminan ulang', 'tawidh bruto',
        "ta'widh bruto", 'laba setelah pajak', 'laba tahun berjalan',
        'jumlah pendapatan kafalah', 'pendapatan underwriting', 'beban kafalah',
        'hasil underwriting neto', 'laba sebelum pajak', 'laba usaha',
        'pendapatan jasa penjaminan (ijk)', 'hasil investasi',
        'pendapatan premi', 'premi bruto', 'beban klaim', 'klaim bruto',
        'laba bersih', 'beban reasuransi', 'beban usaha', 'beban operasional',
        'laba (rugi)', 'pendapatan penjaminan'
    }

    gearing_keywords = {
        'nilai penjaminan ditanggung sendiri', 'modal sendiri bersih',
        'gearing ratio', 'gearing ratio (nilai baris 1:2)', 'gearing ratio aktual'
    }

    cf_keywords = {
        'arus kas dari aktivitas', 'kas bersih', 'arus kas bersih', 'aktivitas operasi',
        'aktivitas investasi', 'aktivitas pendanaan', 'arus kas', 'posisi arus kas', 'posisi arus kas (cashflow)'
    }

    huw_keywords = {
        'hasil underwriting', 'mikro pnm', 'kur mikro', 'retail & korporasi',
        'kur super mikro'
    }

    # Check matches count (substring-based: a keyword counts if any cell contains it)
    def count_matches(keywords):
        return sum(1 for k in keywords if any(k in s for s in text_set))

    bs_matches = count_matches(bs_keywords)
    pl_matches = count_matches(pl_keywords)
    gearing_matches = any(k in s for s in text_set for k in gearing_keywords)
    cf_matches = any(k in s for s in text_set for k in cf_keywords)
    huw_matches = count_matches(huw_keywords)
    mitra_matches = any(k in s for s in text_set for k in ['mitra', 'plafon'])
    
    results = []
    if bs_matches >= 3:
        results.append('BS')
    if pl_matches >= 3:
        results.append('PL')
    if gearing_matches:
        results.append('Gearing')
    if cf_matches:
        results.append('CF')
    if huw_matches >= 2:
        results.append('HUW')
    if any(x in text_set for x in ['plafon penjaminan per mitra', 'plafon penjaminan', 'plafon mitra']) or (mitra_matches and any('plafon' in x for x in text_set)):
        results.append('Mitra')
        
    if not results:
        return ['unknown']
    return results

def detect_excel_type(file_path):
    """
    Detect the type of JPAS Excel file uploaded based on its sheet names and cell contents.
    Returns:
        str: 'worksheet_financial' | 'evaluasi_anper' | 'rekapan_kafalah' | 'unknown'
    """
    try:
        xl = pd.ExcelFile(file_path)
        sheets = xl.sheet_names
        
        # 1. Strict Sheet Name Keyword Check
        if any('Summary PL KONSOL' in s for s in sheets) or any('Summary BS KONSOL' in s for s in sheets):
            return 'worksheet_financial'
        elif any('Input PL' in s for s in sheets) or any('Input BS' in s for s in sheets):
            return 'evaluasi_anper'
        elif any('plafond' in s for s in sheets) or any('pivot 1' in s for s in sheets):
            return 'rekapan_kafalah'
            
        # 2. Content-Based Classification Fallback (scan up to first few sheets)
        found_types = set()
        for sheet in sheets[:5]:
            df = xl.parse(sheet, nrows=50)
            stypes = classify_sheet_by_content(df)
            if isinstance(stypes, str):
                stypes = [stypes]
            for stype in stypes:
                if stype != 'unknown':
                    found_types.add(stype)
                
        if 'BS' in found_types and 'PL' in found_types:
            return 'worksheet_financial'
        elif 'BS' in found_types or 'PL' in found_types:
            # If at least BS or PL is found (like in Rasio Likuiditas 2026.xlsx which has BS)
            return 'worksheet_financial'
        elif 'Gearing' in found_types:
            return 'evaluasi_anper'
        elif 'Mitra' in found_types:
            return 'rekapan_kafalah'
            
        # 3. Fallback check on sheet names containing Plafon/Kafalah
        if any('plafon' in s.lower() or 'mitra' in s.lower() for s in sheets):
            return 'worksheet_financial'
            
        return 'unknown'
    except Exception:
        logger.exception("Error detecting file type for '%s'", file_path)
        return 'unknown'

def format_id(val, is_pct=False, is_currency=False, is_ratio=False, decimals=2, prefix="", suffix=""):
    """
    Format a numeric value in Indonesian style:
    - Dot as thousands separator
    - Comma as decimal separator
    - Handle strings, NaNs, floats, ints
    """
    if pd.isna(val) or val is None:
        return "-"
    
    try:
        if isinstance(val, str):
            val_clean = val.replace('Rp', '').replace('%', '').replace('x', '').replace(' ', '').strip()
            if not val_clean:
                return "-"
            if ',' in val_clean and '.' in val_clean:
                if val_clean.rfind('.') > val_clean.rfind(','):
                    val_clean = val_clean.replace(',', '')
                else:
                    val_clean = val_clean.replace('.', '').replace(',', '.')
            elif ',' in val_clean:
                parts = val_clean.split(',')
                if len(parts[-1]) == 3:
                    val_clean = val_clean.replace(',', '')
                else:
                    val_clean = val_clean.replace(',', '.')
            elif '.' in val_clean:
                parts = val_clean.split('.')
                if len(parts[-1]) == 3:
                    val_clean = val_clean.replace('.', '')
                else:
                    pass
            val_num = float(val_clean)
        else:
            val_num = float(val)
    except Exception:
        return str(val)
        
    if abs(val_num) < 1e-9:
        return "-"
        
    fmt_str = f"{{:,.{decimals}f}}"
    formatted = fmt_str.format(val_num)
    
    parts = formatted.split('.')
    thousands = parts[0].replace(',', '.')
    if len(parts) > 1:
        decimal = parts[1]
        result = f"{thousands},{decimal}"
    else:
        result = thousands
        
    if is_pct:
        return f"{prefix}{result}%{suffix}"
    elif is_currency:
        return f"Rp{prefix}{result}{suffix}"
    elif is_ratio:
        return f"{prefix}{result}x{suffix}"
    return f"{prefix}{result}{suffix}"

