import os
import logging
import pandas as pd
import numpy as np
import re
import datetime

logger = logging.getLogger("jpas.parser")

def normalize_string(s):
    """Normalize string by removing extra spaces, newlines, and converting to lowercase"""
    return re.sub(r'\s+', ' ', str(s).strip().lower())

# ---------------------------------------------------------------------------
# Fuzzy account-label matching (fallback when the exact label chains miss)
# ---------------------------------------------------------------------------
# Words that carry no meaning for account identification
_LABEL_STOPWORDS = {'dan', 'yang', 'di', 'pada', 'dalam', 'dari', 'ke', 'atas',
                    'untuk', 'per', 'rugi', 'the', 'of', 'and', 'a'}

def _tokenize_label(s):
    tokens = re.split(r'[^a-z0-9]+', str(s).lower())
    return {t for t in tokens if t and t not in _LABEL_STOPWORDS}

def _fuzzy_match_account(label_norm, synonym_table):
    """
    Token-subset matcher: a synonym phrase matches when every significant word
    of the phrase appears somewhere in the row label, regardless of word order
    or extra words. Single-word phrases must match the label exactly (too
    ambiguous otherwise). Returns the canonical key with the most specific
    (longest) matching phrase, or None.
    """
    label_tokens = _tokenize_label(label_norm)
    if not label_tokens:
        return None
    best_key, best_score = None, 0
    for key, phrases, excludes in synonym_table:
        if any(x in label_norm for x in excludes):
            continue
        for phrase in phrases:
            ptokens = _tokenize_label(phrase)
            if len(ptokens) <= 1:
                if label_norm != phrase:
                    continue
                score = 1.5
            elif ptokens <= label_tokens:
                score = len(ptokens)
            else:
                continue
            if score > best_score:
                best_key, best_score = key, score
    if best_key:
        logger.info("Fuzzy-matched account label '%s' -> '%s'", label_norm, best_key)
    return best_key

# Synonym variants NOT covered by the exact chains in parse_unified_pl_sheet.
# Format: (canonical_key, [phrase variants], [exclusion substrings])
PL_FALLBACK_SYNONYMS = [
    ('ijk_revenue', ['pendapatan ijk', 'premi bruto', 'pendapatan premi bruto',
                     'pendapatan bruto penjaminan', 'imbal jasa kafalah',
                     'gross premium', 'kontribusi bruto'], ['ybmp', 'ditangguhkan', 'piutang', 'utang']),
    ('reinsurance_expense', ['premi reasuransi', 'beban reasuransi', 'kontribusi reasuransi',
                             'reasuransi keluar'], ['komisi', 'klaim', 'tawidh', 'piutang', 'utang', 'aset']),
    ('change_unearned_ijk', ['perubahan premi belum merupakan pendapatan',
                             'perubahan ujrah ybmp', 'perubahan ijk ybmp'], []),
    ('net_underwriting_revenue', ['pendapatan premi neto', 'premi neto', 'net premium',
                                  'pendapatan bersih penjaminan'], []),
    ('gross_claims', ['beban klaim bruto', 'klaim bruto', 'gross claims'], ['reasuransi', 'retensi', 'piutang', 'utang', 'cadangan']),
    ('reinsurance_claims', ['klaim reasuransi', 'tawidh reasuransi'], ['piutang', 'utang']),
    ('net_recoveries', ['pendapatan subrogasi', 'recoveries bersih', 'hasil subrogasi'], []),
    ('net_underwriting_result', ['laba underwriting', 'surplus underwriting',
                                 'surplus defisit underwriting'], []),
    ('investment_income', ['pendapatan investasi', 'investment income',
                           'hasil investasi bersih'], ['piutang', 'beban']),
    ('total_operating_expense', ['beban operasional', 'biaya operasional',
                                 'beban umum administrasi', 'opex'], ['pendapatan']),
    ('operating_profit', ['laba operasional', 'ebit'], ['sebelum', 'setelah']),
    ('pretax_profit', ['laba sebelum pajak penghasilan', 'ebt'], []),
    ('net_profit', ['laba bersih', 'laba periode berjalan', 'net income',
                    'laba setelah pajak penghasilan'], ['komprehensif', 'ditahan', 'saldo']),
]

# Synonym variants for aggregate balance-sheet accounts (section-independent).
BS_FALLBACK_SYNONYMS = [
    ('cash_and_bank', ['kas setara kas', 'kas bank', 'cash equivalents', 'kas giro'], ['arus', 'bersih']),
    ('total_current_assets', ['total aset lancar', 'jumlah aktiva lancar', 'total aktiva lancar',
                              'total current assets'], []),
    ('total_assets', ['total aktiva', 'jumlah aktiva', 'total assets'], ['lancar', 'tetap', 'reasuransi', 'pajak', 'lain']),
    ('total_current_liabilities', ['total liabilitas lancar', 'jumlah kewajiban lancar',
                                   'jumlah kewajiban jangka pendek', 'total kewajiban lancar'], []),
    ('total_liabilities', ['total kewajiban', 'jumlah kewajiban', 'total liabilities'],
     ['ekuitas', 'modal', 'lancar', 'jangka', 'lain']),
    ('total_equity', ['total modal sendiri', 'jumlah modal sendiri', 'total equity'], ['liabilitas', 'kewajiban', 'disetor', 'bersih']),
    ('fixed_assets', ['aktiva tetap', 'property and equipment'], []),
    ('claims_reserve', ['liabilitas klaim', 'cadangan teknis klaim'], ['piutang', 'utang klaim']),
    ('piutang_ijk_lancar', ['piutang premi', 'piutang ujrah', 'piutang kontribusi'], ['ckpn', 'reasuransi']),
    ('capital', ['modal saham', 'share capital'], []),
    ('retained_earnings', ['laba ditahan'], []),
]

def clean_value(val):
    """Convert value to float if possible, otherwise return 0.0"""
    if pd.isna(val):
        return 0.0
    try:
        if isinstance(val, (int, float, np.number)):
            return float(val)
        val_str = str(val).strip().replace('.', '').replace(',', '.')
        return float(val_str)
    except ValueError:
        return 0.0

def find_sheet_by_keyword(xl, keyword):
    """Find sheet name containing a keyword (case insensitive)"""
    for sheet in xl.sheet_names:
        if keyword.lower() in sheet.lower():
            return sheet
    return None

def make_columns_unique(cols):
    """Make column list unique by appending a suffix for duplicates"""
    seen = {}
    new_cols = []
    for col in cols:
        col_str = str(col).strip()
        if col_str in seen:
            seen[col_str] += 1
            new_cols.append(f"{col_str}_{seen[col_str]}")
        else:
            seen[col_str] = 0
            new_cols.append(col_str)
    return new_cols

def format_date_header(val):
    if isinstance(val, (pd.Timestamp, datetime.datetime)):
        months_id = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
        return f"{months_id[val.month]} {val.year}"
    val_str = str(val).strip()
    match = re.match(r'^(\d{4})-(\d{2})-(\d{2}).*$', val_str)
    if match:
        year = match.group(1)
        month_num = int(match.group(2))
        months_id = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
        return f"{months_id[month_num]} {year}"
    return val_str

def _generate_combined_headers(row_top, row_sub):
    """Generate combined unique column headers from top and sub rows."""
    num_cols = len(row_top)
    curr_top = ""
    headers = []
    for col_i in range(num_cols):
        val_top = str(row_top[col_i]).strip() if pd.notna(row_top[col_i]) else ""
        if val_top != "" and val_top.lower() != 'nan':
            curr_top = val_top
            
        val_sub = row_sub[col_i]
        val_sub_str = format_date_header(val_sub) if pd.notna(val_sub) else ""
        if val_sub_str.lower() == 'nan':
            val_sub_str = ""
            
        if curr_top != "" and val_sub_str != "":
            if val_sub_str.lower() in curr_top.lower():
                combined = curr_top
            elif curr_top.lower() in val_sub_str.lower():
                combined = val_sub_str
            else:
                combined = f"{curr_top} - {val_sub_str}"
        elif curr_top != "":
            combined = curr_top
        elif val_sub_str != "":
            combined = val_sub_str
        else:
            combined = f"Col_{col_i}"
            
        combined = re.sub(r'\s+', ' ', combined).strip()
        headers.append(combined)
        
    return make_columns_unique(headers)

def _extract_header_and_data(df, row_top, row_sub, data_start_idx, desc_col_idx=0):
    unique_headers = _generate_combined_headers(row_top, row_sub)
    desc_col_name = unique_headers[desc_col_idx]
    
    df_data = df.iloc[data_start_idx:].copy()
    df_data.columns = unique_headers
    
    df_data = df_data[df_data[desc_col_name].notna()]
    df_data = df_data[df_data[desc_col_name].astype(str).str.strip() != '']
    df_data = df_data[~df_data[desc_col_name].astype(str).str.strip().str.lower().isin(['nan', 'none', 'keterangan', 'uraian'])]
    df_data = df_data[~df_data[desc_col_name].astype(str).str.strip().str.isdigit()]
    
    cols_to_keep = [c for c in df_data.columns if not any(x in c.lower() for x in ['column', 'unnamed'])]
    df_data = df_data[cols_to_keep]
    
    for col in df_data.columns[1:]:
        df_data[col] = df_data[col].apply(clean_value)
        
    return df_data

def filter_statement_columns(df):
    cols_to_keep = []
    cols_to_keep.append(df.columns[0])
    
    for col in df.columns[1:]:
        col_lower = col.lower()
        if col_lower.replace('_','').replace('.','').isdigit():
            continue
        if '2024' in col_lower:
            continue
        is_monthly_2025 = re.search(r'(januari|februari|maret|april|juni|juli|agustus|september|oktober|november)\s+2025', col_lower)
        if is_monthly_2025:
            continue
        is_monthly_2026 = re.search(r'(januari|februari|maret|juni|juli|agustus|september|oktober|november|desember)\s+2026', col_lower)
        if is_monthly_2026 and 's.d' not in col_lower:
            continue
            
        cols_to_keep.append(col)
        
    return df[cols_to_keep]

def parse_full_statement_sheet(xl, keyword):
    sheet_name = None
    for s in xl.sheet_names:
        if keyword.lower() in s.lower():
            sheet_name = s
            break
            
    if not sheet_name:
        return None
        
    df = xl.parse(sheet_name, header=None)
    
    key_row_idx = None
    desc_col_idx = None
    for idx, row in df.iterrows():
        row_vals = [str(x).lower().strip() for x in row.values]
        for col_i, val in enumerate(row_vals):
            if val in ['keterangan', 'uraian']:
                key_row_idx = idx
                desc_col_idx = col_i
                break
        if key_row_idx is not None:
            break
            
    if key_row_idx is None:
        return None
        
    has_top_header = False
    if key_row_idx > 0:
        row_above = [str(x).lower().strip() for x in df.iloc[key_row_idx - 1].values]
        if any(any(k in x for k in ['update', 'realisasi', 'anggaran', 'rkap', 'audited']) for x in row_above if x != 'nan'):
            has_top_header = True
            
    if has_top_header:
        top_row_idx = key_row_idx - 1
        sub_row_idx = key_row_idx
        data_start_idx = key_row_idx + 1
    else:
        top_row_idx = key_row_idx
        sub_row_idx = key_row_idx + 1
        data_start_idx = key_row_idx + 2
        
    if data_start_idx < df.shape[0]:
        row_check = [str(x).strip() for x in df.iloc[data_start_idx].values if pd.notna(x)]
        if len(row_check) > 0 and all(x.isdigit() for x in row_check if x != ''):
            data_start_idx += 1
            
    row_top = df.iloc[top_row_idx].tolist()
    row_sub = df.iloc[sub_row_idx].tolist()
    
    df_data = _extract_header_and_data(df, row_top, row_sub, data_start_idx, desc_col_idx)
    
    # Filter empty/zero columns
    desc_col_name = df_data.columns[0]
    active_cols = [desc_col_name]
    for col in df_data.columns[1:]:
        if df_data[col].abs().sum() > 0:
            active_cols.append(col)
            
    return df_data[active_cols].copy()

def parse_cash_flow_sheet(xl, sheet_name=None):
    """Parse the Cash Flow sheet from any JPAS Excel file"""
    cfs_sheet = sheet_name or (
        find_sheet_by_keyword(xl, 'Posisi Arus Kas') or
        find_sheet_by_keyword(xl, 'Lap. AK') or
        find_sheet_by_keyword(xl, 'CASH FLOW') or
        find_sheet_by_keyword(xl, 'Arus Kas') or
        find_sheet_by_keyword(xl, 'Input CF') or
        find_sheet_by_keyword(xl, 'CF')
    )
    if not cfs_sheet:
        return None
    df = xl.parse(cfs_sheet, header=None)
    
    # Drop columns that are completely empty to fix column shifting issues
    df.dropna(how='all', axis=1, inplace=True)
    
    # Find header row dynamically
    header_idx = None
    for idx, row in df.iterrows():
        row_vals = [str(x).lower().strip() for x in row.values]
        has_label_key = any(val in ['keterangan', 'uraian', 'arus kas', 'pos', 'kegiatan', 'aktivitas'] for val in row_vals)
        has_fin_key = any('realisasi' in x or 'anggaran' in x or 'rkap' in x or 'audited' in x or '2024' in x or '2025' in x or '2026' in x or 'ytd' in x for x in row_vals)
        if has_label_key and has_fin_key:
            header_idx = idx
            break
            
    if header_idx is None:
        return None
        
    # Check if double-row header
    is_double_header = False
    desc_col_idx = 0
    if header_idx + 1 < len(df):
        row_below = df.iloc[header_idx + 1]
        desc_val_below = row_below.iloc[desc_col_idx]
        desc_empty = pd.isna(desc_val_below) or str(desc_val_below).strip() == "" or str(desc_val_below).strip().lower() == 'nan'
        
        string_keywords = 0
        numeric_values = 0
        for col_i, val in enumerate(row_below.values):
            if col_i == desc_col_idx:
                continue
            if pd.isna(val):
                continue
            val_str = str(val).lower().strip()
            if val_str == "" or val_str == 'nan':
                continue
            try:
                if isinstance(val, (int, float, np.number)):
                    float(val)
                else:
                    float(str(val).strip().replace('.', '').replace(',', '.'))
                numeric_values += 1
            except (ValueError, TypeError):
                string_keywords += 1
                
        if desc_empty and string_keywords > 0 and numeric_values == 0:
            is_double_header = True

    if is_double_header:
        row_top = df.iloc[header_idx].tolist()
        row_sub = df.iloc[header_idx + 1].tolist()
        data_start_idx = header_idx + 2
    else:
        row_top = df.iloc[header_idx].tolist()
        row_sub = df.iloc[header_idx].tolist()
        data_start_idx = header_idx + 1

    return _extract_header_and_data(df, row_top, row_sub, data_start_idx, desc_col_idx)

def parse_huw_sheet(xl, sheet_name=None):
    """Parse the Input HUW sheet containing underwriting drivers"""
    huw_sheet = sheet_name or find_sheet_by_keyword(xl, 'Input HUW')
    if not huw_sheet:
        return None
    df = xl.parse(huw_sheet)
    if len(df) < 2:
        return None
    # Guard: reject pivot/mitra sheets misclassified as HUW.
    # A valid HUW sheet must have numeric data in the majority of cells.
    # Pivot/mitra sheets have many text cells like "Row Labels", bank names, etc.
    sample = df.iloc[:min(10, len(df)), 1:]
    text_cells = sum(1 for _, row in sample.iterrows()
                     for v in row if isinstance(v, str) and v.strip() and v.strip().lower() not in ['nan', 'none'])
    numeric_cells = sum(1 for _, row in sample.iterrows()
                        for v in row if isinstance(v, (int, float)) and not pd.isna(v))
    if text_cells > numeric_cells and text_cells > 5:
        return None
        
    raw_headers = [str(x).strip().replace('\n', ' ') for x in df.iloc[1].values]
    unique_headers = []
    seen = {}
    for i, h in enumerate(raw_headers):
        if h in ['nan', 'None', '']:
            if i == 1:
                h_str = 'Indikator'
            else:
                h_str = f'Col_{i}'
        else:
            h_str = h
        if h_str in seen:
            seen[h_str] += 1
            unique_headers.append(f"{h_str}_{seen[h_str]}")
        else:
            seen[h_str] = 0
            unique_headers.append(h_str)
            
    df_sliced = df.iloc[2:].copy()
    df_sliced.columns = unique_headers
    
    # Drop columns starting with Col_
    cols_to_keep = [c for c in df_sliced.columns if not c.startswith('Col_')]
    df_sliced = df_sliced[cols_to_keep]
    
    if 'Indikator' not in df_sliced.columns:
        return None
        
    # Remove rows where Indikator is nan or empty
    df_sliced = df_sliced[df_sliced['Indikator'].notna()]
    df_sliced = df_sliced[df_sliced['Indikator'].astype(str).str.strip() != '']
    df_sliced = df_sliced[df_sliced['Indikator'].astype(str).str.strip() != 'Hijau = sudah diupdate']
    
    # Label Row 20 (Hasil Underwriting Neto) which has nan label
    for i, row in df_sliced.iterrows():
        if str(row['Indikator']).strip().lower() in ['nan', 'none', '']:
            df_sliced.loc[i, 'Indikator'] = "Hasil Underwriting Neto"
            
    # Clean numeric columns and convert to Jutaan Rp (divide by 1,000,000)
    for col in df_sliced.columns[1:]:
        df_sliced[col] = df_sliced[col].apply(lambda x: clean_value(x) / 1_000_000.0)
        
    # Rename duplicate YoY columns to make them readable
    clean_cols = []
    for c in df_sliced.columns:
        if c.endswith('_1'):
            clean_cols.append(c[:-2] + " (YoY)")
        else:
            clean_cols.append(c)
    df_sliced.columns = clean_cols
    
    return df_sliced

def parse_worksheet_financial(file_path):
    """
    Parse Worksheet JPAS Data Financial Mei 2026 / JPAS_Analysis_Enhanced.xlsx
    """
    xl = pd.ExcelFile(file_path)
    
    # --- Parse PL ---
    pl_sheet = find_sheet_by_keyword(xl, 'Summary PL KONSOL')
    pl_data = {}
    if pl_sheet:
        df_pl = xl.parse(pl_sheet)
        header_row_idx = None
        for idx, row in df_pl.iterrows():
            if any('keterangan' in str(val).lower() for val in row):
                header_row_idx = idx
                break
        
        if header_row_idx is not None:
            col_indices = {
                'keterangan': 0,
                'ytd_prev_month': 3,
                'ytd_curr_month': 5,
                'ytd_yoy_prev': 7,
                'rkap_ytd': 17,
                'rkap_fy': 21
            }
            
            pl_mapping = {
                'pendapatan jasa penjaminan (ijk)': 'ijk_revenue',
                'beban penjaminan ulang': 'reinsurance_expense',
                'komisi penjaminan ulang': 'reinsurance_commission',
                '(kenaikan) penurunan ijk yang belum merupakan pendapatan': 'change_unearned_ijk',
                'penurunan (kenaikan) ijk ybmp': 'change_unearned_ijk',
                'pendapatan underwriting': 'net_underwriting_revenue',
                'pendapatan underwriting lain': 'other_underwriting_revenue',
                'ta\'widh bruto': 'gross_claims',
                'ta\'widh reasuransi': 'reinsurance_claims',
                'kenaikan (penurunan) estimasi tawidh retensi sendiri': 'change_claims_retention',
                'pendapatan recoveries - bersih': 'net_recoveries',
                'recoveries bersih': 'net_recoveries',
                'beban komisi': 'commission_expense',
                'beban underwriting lain': 'other_underwriting_expense',
                'hasil underwriting neto': 'net_underwriting_result',
                'hasil investasi': 'investment_income',
                'total beban usaha': 'total_operating_expense',
                'laba usaha': 'operating_profit',
                'laba sebelum pajak': 'pretax_profit',
                'laba setelah pajak': 'net_profit'
            }
            
            for idx in range(header_row_idx + 1, len(df_pl)):
                row_label = normalize_string(df_pl.iloc[idx, 0])
                matched_key = pl_mapping.get(row_label)
                
                if matched_key:
                    pl_data[matched_key] = {
                        'prev_month': clean_value(df_pl.iloc[idx, col_indices['ytd_prev_month']]),
                        'curr_month': clean_value(df_pl.iloc[idx, col_indices['ytd_curr_month']]),
                        'yoy_prev': clean_value(df_pl.iloc[idx, col_indices['ytd_yoy_prev']]),
                        'rkap_ytd': clean_value(df_pl.iloc[idx, col_indices['rkap_ytd']]),
                        'rkap_fy': clean_value(df_pl.iloc[idx, col_indices['rkap_fy']])
                    }
                    
    # --- Parse BS ---
    bs_sheet = find_sheet_by_keyword(xl, 'Summary BS KONSOL')
    bs_data = {}
    if bs_sheet:
        df_bs = xl.parse(bs_sheet)
        header_row_idx = None
        for idx, row in df_bs.iterrows():
            if any('keterangan' in str(val).lower() for val in row):
                header_row_idx = idx
                break
        
        if header_row_idx is not None:
            col_indices = {
                'keterangan': 0,
                'prev_month': 3,
                'curr_month': 5,
                'prev_year_yoy': 8
            }
            
            bs_mapping = {
                'kas dan bank': 'cash_and_bank',
                'piutang imbal jasa kafalah': 'ijk_receivable',
                'piutang ta\'widh': 'claims_receivable',
                'aset reasuransi': 'reinsurance_assets',
                'deposito berjangka mudharabah': 'deposito_invest',
                'reksadana tersedia untuk dijual': 'reksadana_invest',
                'reksadana': 'reksadana_invest',
                'surat berharga syariah negara': 'sbsn_invest',
                'aset tetap': 'fixed_assets',
                'aset': 'total_assets',
                'liabilitas': 'total_liabilities',
                'estimasi ujrah yang belum merupakan pendapatan': 'unearned_premium_reserve',
                'estimasi cad. premi': 'unearned_premium_reserve',
                'estimasi ta\'widh retensi sendiri': 'claims_reserve_retention',
                'pendapatan komisi yang ditangguhkan': 'deferred_commission_income',
                'ekuitas': 'total_equity'
            }
            
            for idx in range(header_row_idx + 1, len(df_bs)):
                row_label = normalize_string(df_bs.iloc[idx, 0])
                matched_key = bs_mapping.get(row_label)
                
                if matched_key:
                    bs_data[matched_key] = {
                        'prev_month': clean_value(df_bs.iloc[idx, col_indices['prev_month']]),
                        'curr_month': clean_value(df_bs.iloc[idx, col_indices['curr_month']]),
                        'prev_year_yoy': clean_value(df_bs.iloc[idx, col_indices['prev_year_yoy']])
                    }
                    
    # --- Parse Gearing Ratio ---
    gr_sheet = find_sheet_by_keyword(xl, 'Gearing Ratio')
    gr_data = {}
    if gr_sheet:
        df_gr = xl.parse(gr_sheet)
        os_net = 0.0
        equity = 0.0
        gearing = 0.0
        
        for idx, row in df_gr.iterrows():
            label = str(row.iloc[1]).strip().lower()
            if 'nilai penjaminan' in label or 'ditanggung sendiri' in label:
                os_net = clean_value(row.iloc[5])
                if os_net > 1_000_000_000:
                    os_net = os_net / 1_000_000
            elif 'modal sendiri' in label or 'ekuitas' in label:
                equity = clean_value(row.iloc[5])
                if equity > 1_000_000_000:
                    equity = equity / 1_000_000
            elif 'gearing ratio' in label:
                gearing = clean_value(row.iloc[5])
                
        if os_net == 0.0 and 'total_equity' in bs_data:
            equity = bs_data['total_equity']['curr_month']
        
        gr_data = {
            'os_net': os_net,
            'equity': equity,
            'gearing_ratio': gearing if gearing > 0 else (os_net / equity if equity > 0 else 0)
        }

    # --- Parse Plafon Mitra (Partner Concentration) ---
    mitra_sheet = find_sheet_by_keyword(xl, 'Plafon Penjaminan per Mitra') or find_sheet_by_keyword(xl, 'Mitra')
    mitra_list = []
    if mitra_sheet:
        df_mitra = xl.parse(mitra_sheet)
        header_idx = None
        for idx, row in df_mitra.iterrows():
            if any('mitra' in str(val).lower() or 'bank' in str(val).lower() for val in row):
                header_idx = idx
                break
        
        if header_idx is not None:
            for idx in range(header_idx + 1, len(df_mitra)):
                partner_name = str(df_mitra.iloc[idx, 3]).strip()
                if partner_name in ['nan', 'None', '', 'GRAND TOTAL', 'TOTAL']:
                    continue
                
                os_kafalah = clean_value(df_mitra.iloc[idx, 4])
                os_penjaminan = clean_value(df_mitra.iloc[idx, 5])
                
                if os_kafalah > 1_000_000_000:
                    os_kafalah /= 1_000_000
                if os_penjaminan > 1_000_000_000:
                    os_penjaminan /= 1_000_000
                    
                mitra_list.append({
                    'partner': partner_name,
                    'os_kafalah_juta': os_kafalah,
                    'os_penjaminan_juta': os_penjaminan
                })
    pl_df = parse_full_statement_sheet(xl, 'Summary PL KONSOL')
    if pl_df is not None:
        pl_df = filter_statement_columns(pl_df)
        
    bs_df = parse_full_statement_sheet(xl, 'Summary BS KONSOL')
    if bs_df is not None:
        bs_df = filter_statement_columns(bs_df)
        
    cfs_data = parse_cash_flow_sheet(xl)
    huw_data = parse_huw_sheet(xl)
    
    return {
        'pl_data': pl_data,
        'bs_data': bs_data,
        'gr_data': gr_data,
        'mitra_data': mitra_list,
        'cfs_data': cfs_data,
        'huw_data': huw_data,
        'pl_df': pl_df,
        'bs_df': bs_df
    }

def parse_evaluasi_anper(file_path):
    """
    Parse Worksheet Rapat Evaluasi Anper Mei 2026
    """
    xl = pd.ExcelFile(file_path)
    
    # --- Parse PL ---
    pl_sheet = find_sheet_by_keyword(xl, 'Input PL')
    pl_data = {}
    if pl_sheet:
        df_pl = xl.parse(pl_sheet)
        header_row_idx = None
        for idx, row in df_pl.iterrows():
            if any('uraian' in str(val).lower() for val in row):
                header_row_idx = idx
                break
                
        if header_row_idx is not None:
            col_indices = {
                'keterangan': 1,
                'ytd_prev_month': 4,
                'ytd_curr_month': 5,
                'ytd_yoy_prev': 2,
                'rkap_ytd': 5,
                'rkap_fy': 3
            }
            
            pl_mapping = {
                'imbal jasa kafalah bruto': 'ijk_revenue',
                'beban penjaminan ulang': 'reinsurance_expense',
                'komisi penjaminan ulang': 'reinsurance_commission',
                'penurunan (kenaikan) ijk ybmp': 'change_unearned_ijk',
                'jumlah pendapatan kafalah': 'net_underwriting_revenue',
                'ta\'widh': 'gross_claims',
                'tawidh': 'gross_claims',
                'ta\'widh reas': 'reinsurance_claims',
                'tawidh reas': 'reinsurance_claims',
                'kenaikan (penurunan) estimasi tawidh retensi sendiri': 'change_claims_retention',
                'penerimaan recoveries': 'net_recoveries',
                'recoveries': 'net_recoveries',
                'biaya komisi': 'commission_expense',
                'jumlah beban kafalah': 'net_underwriting_result_proxy',
                'hasil investasi': 'investment_income',
                'jumlah beban usaha': 'total_operating_expense',
                'laba (rugi) usaha': 'operating_profit',
                'laba sebelum pajak': 'pretax_profit',
                'laba setelah pajak': 'net_profit'
            }
            
            for idx in range(header_row_idx + 1, len(df_pl)):
                row_label = normalize_string(df_pl.iloc[idx, 1])
                matched_key = pl_mapping.get(row_label)
                
                if matched_key:
                    pl_data[matched_key] = {
                        'prev_month': clean_value(df_pl.iloc[idx, col_indices['ytd_prev_month']]),
                        'curr_month': clean_value(df_pl.iloc[idx, col_indices['ytd_curr_month']]),
                        'yoy_prev': clean_value(df_pl.iloc[idx, col_indices['ytd_yoy_prev']]),
                        'rkap_ytd': clean_value(df_pl.iloc[idx, col_indices['ytd_curr_month']]) * 0.9,
                        'rkap_fy': clean_value(df_pl.iloc[idx, col_indices['rkap_fy']])
                    }
            
            if 'net_underwriting_result' not in pl_data and 'ijk_revenue' in pl_data:
                curr_uw_rev = pl_data['ijk_revenue']['curr_month'] + pl_data.get('reinsurance_expense', {}).get('curr_month', 0.0) + pl_data.get('reinsurance_commission', {}).get('curr_month', 0.0) + pl_data.get('change_unearned_ijk', {}).get('curr_month', 0.0)
                curr_claims = pl_data.get('gross_claims', {}).get('curr_month', 0.0) + pl_data.get('reinsurance_claims', {}).get('curr_month', 0.0)
                curr_recoveries = pl_data.get('net_recoveries', {}).get('curr_month', 0.0)
                curr_commission = pl_data.get('commission_expense', {}).get('curr_month', 0.0)
                
                pl_data['net_underwriting_revenue'] = {
                    'prev_month': 0.0,
                    'curr_month': curr_uw_rev,
                    'yoy_prev': 0.0,
                    'rkap_ytd': 0.0,
                    'rkap_fy': pl_data.get('net_underwriting_revenue', {}).get('rkap_fy', 0.0)
                }
                
                pl_data['net_underwriting_result'] = {
                    'prev_month': 0.0,
                    'curr_month': curr_uw_rev + curr_claims + curr_recoveries - curr_commission,
                    'yoy_prev': 0.0,
                    'rkap_ytd': 0.0,
                    'rkap_fy': 0.0
                }

    # --- Parse BS ---
    bs_sheet = find_sheet_by_keyword(xl, 'Input BS')
    bs_data = {}
    if bs_sheet:
        df_bs = xl.parse(bs_sheet)
        header_row_idx = None
        for idx, row in df_bs.iterrows():
            if any('uraian' in str(val).lower() for val in row):
                header_row_idx = idx
                break
                
        if header_row_idx is not None:
            col_indices = {
                'keterangan': 1,
                'prev_month': 4,
                'curr_month': 5,
                'prev_year_yoy': 2
            }
            
            bs_mapping = {
                'kas dan bank': 'cash_and_bank',
                'piutang imbal jasa kafalah': 'ijk_receivable',
                'piutang tawidh': 'claims_receivable',
                'piutang ta\'widh': 'claims_receivable',
                'aset reas': 'reinsurance_assets',
                'deposito berjangka mudharabah': 'deposito_invest',
                'reksadana': 'reksadana_invest',
                'surat berharga syariah negara': 'sbsn_invest',
                'jumlah aset': 'total_assets',
                'jumlah liabilitas': 'total_liabilities',
                'estimasi cad. premi': 'unearned_premium_reserve',
                'estimasi ujrah yang belum merupakan pendapatan': 'unearned_premium_reserve',
                'estimasi tawidh retensi sendiri': 'claims_reserve_retention',
                'estimasi ta\'widh retensi sendiri': 'claims_reserve_retention',
                'pendapatan komisi yang ditangguhkan': 'deferred_commission_income',
                'jumlah ekuitas': 'total_equity'
            }
            
            for idx in range(header_row_idx + 1, len(df_bs)):
                row_label = normalize_string(df_bs.iloc[idx, 1])
                matched_key = bs_mapping.get(row_label)
                
                if matched_key:
                    bs_data[matched_key] = {
                        'prev_month': clean_value(df_bs.iloc[idx, col_indices['prev_month']]),
                        'curr_month': clean_value(df_bs.iloc[idx, col_indices['curr_month']]),
                        'prev_year_yoy': clean_value(df_bs.iloc[idx, col_indices['prev_year_yoy']])
                    }

    # --- Gearing Ratio ---
    gr_sheet = find_sheet_by_keyword(xl, 'Gearing Ratio')
    gr_data = {}
    if gr_sheet:
        df_gr = xl.parse(gr_sheet)
        os_net = 0.0
        equity = 0.0
        gearing = 0.0
        
        for idx, row in df_gr.iterrows():
            label = str(row.iloc[1]).strip().lower()
            if 'ditanggung sendiri' in label or 'nilai penjaminan' in label:
                os_net = clean_value(row.iloc[5])
                if os_net > 1_000_000_000:
                    os_net /= 1_000_000
            elif 'modal sendiri' in label or 'ekuitas' in label:
                equity = clean_value(row.iloc[5])
                if equity > 1_000_000_000:
                    equity /= 1_000_000
            elif 'gearing ratio' in label:
                gearing = clean_value(row.iloc[5])
                
        if os_net == 0.0 and 'total_equity' in bs_data:
            equity = bs_data['total_equity']['curr_month']
            
        gr_data = {
            'os_net': os_net,
            'equity': equity,
            'gearing_ratio': gearing if gearing > 0 else (os_net / equity if equity > 0 else 0)
        }
    else:
        eq_val = bs_data.get('total_equity', {}).get('curr_month', 0.0)
        gr_data = {
            'os_net': 0.0,
            'equity': eq_val,
            'gearing_ratio': 0.0
        }
    pl_df = parse_full_statement_sheet(xl, 'Input PL')
    if pl_df is not None:
        pl_df = filter_statement_columns(pl_df)
        
    bs_df = parse_full_statement_sheet(xl, 'Input BS')
    if bs_df is not None:
        bs_df = filter_statement_columns(bs_df)
        
    cfs_data = parse_cash_flow_sheet(xl)
    huw_data = parse_huw_sheet(xl)
    
    return {
        'pl_data': pl_data,
        'bs_data': bs_data,
        'gr_data': gr_data,
        'mitra_data': [],
        'cfs_data': cfs_data,
        'huw_data': huw_data,
        'pl_df': pl_df,
        'bs_df': bs_df
    }

def parse_rekapan_kafalah(file_path, sheet_name=None):
    """
    Parse Plafond Penjaminan per Mitra Rekap Excel (2026 - REKAPAN KAFALAH.xlsx)
    """
    xl = pd.ExcelFile(file_path)
    pl_sheet = sheet_name or find_sheet_by_keyword(xl, 'plafond') or xl.sheet_names[0]
    df = xl.parse(pl_sheet)
    
    partner_col = None
    kafalah_col = None
    penjaminan_col = None
    
    for col in df.columns:
        col_name = str(col).lower()
        if 'mitra' in col_name or 'bank' in col_name or 'customer' in col_name:
            partner_col = col
        elif 'kafalah' in col_name or 'plafon' in col_name or 'limit' in col_name:
            kafalah_col = col
        elif 'penjaminan' in col_name or 'net' in col_name or 'retensi' in col_name:
            penjaminan_col = col
            
    if partner_col is None:
        for idx, row in df.iloc[:5].iterrows():
            for c_idx, val in enumerate(row):
                val_str = str(val).lower()
                if 'mitra' in val_str or 'bank' in val_str or 'nama' in val_str:
                    partner_col = df.columns[c_idx]
                elif 'kafalah' in val_str or 'plafon' in val_str:
                    kafalah_col = df.columns[c_idx]
                elif 'penjaminan' in val_str:
                    penjaminan_col = df.columns[c_idx]
                    
    if partner_col is None and len(df.columns) >= 3:
        partner_col = df.columns[1]
        kafalah_col = df.columns[2]
        penjaminan_col = df.columns[3] if len(df.columns) > 3 else df.columns[2]

    mitra_list = []
    if partner_col is not None and kafalah_col is not None:
        for idx, row in df.iterrows():
            partner_name = str(row[partner_col]).strip()
            if partner_name in ['nan', 'None', '', 'GRAND TOTAL', 'TOTAL', 'Total']:
                continue
            
            os_kafalah = clean_value(row[kafalah_col])
            os_penjaminan = clean_value(row[penjaminan_col]) if penjaminan_col is not None else os_kafalah * 0.6
            
            if os_kafalah > 1_000_000_000:
                os_kafalah /= 1_000_000
            if os_penjaminan > 1_000_000_000:
                os_penjaminan /= 1_000_000
                
            mitra_list.append({
                'partner': partner_name,
                'os_kafalah_juta': os_kafalah,
                'os_penjaminan_juta': os_penjaminan
            })
            
    mitra_list = sorted(mitra_list, key=lambda x: x['os_kafalah_juta'], reverse=True)
    return {
        'pl_data': {},
        'bs_data': {},
        'gr_data': {},
        'mitra_data': mitra_list,
        'cfs_data': None,
        'huw_data': None
    }

def merge_financial_dicts(target, source):
    """
    Merge two parsed financial dictionaries accumulatively.
    """
    for key, cols in source.items():
        if key not in target:
            target[key] = cols.copy()
        else:
            for col, val in cols.items():
                # Prefer larger absolute values for financial reporting columns to avoid overwriting with zero/empty
                if abs(val) > abs(target[key].get(col, 0.0)):
                    target[key][col] = val
    return target

def classify_columns(df_headers):
    col_mapping = {}
    months_id = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
    
    # Pre-check: is this the mislabeled 2025 file?
    has_mei_2026 = any(any(x in normalize_string(str(h)) for x in ['mei 2026', '2026-05-01', 'mei-26', 'may-26', '31 mei 2026']) for h in df_headers)
    has_mislabeled_mei_2025 = any('januari sd mei 2025' in normalize_string(str(h)) for h in df_headers)
    is_mislabeled_file = (not has_mei_2026) and has_mislabeled_mei_2025
    
    for idx, h in enumerate(df_headers):
        h_norm = normalize_string(h)
        
        # Skip columns that represent ratios/percentages
        if any(x in h_norm for x in ['%', 'rasio', 'ratio', 'persen']):
            continue
            
        # Check if plan/target column (RKAP / Anggaran)
        is_plan = any(x in h_norm for x in ['rkap', 'anggaran', 'target', 'plan'])
        if is_plan:
            if any(x in h_norm for x in ['anggaran 2026', 'rkap 2026', 'rkap fy', 'rkap tahunan']) or (('2026' in h_norm or 'tahun' in h_norm) and not any(y in h_norm for y in ['ytd', 's/d', 's.d', 'bulan', 'proporsional', 'berjalan'])):
                col_mapping['rkap_fy'] = idx
            elif any(x in h_norm for x in ['rkap ytd', 'target ytd', 'ytd rkap', 'rkap s/d', 'target s/d']):
                col_mapping['rkap_ytd'] = idx
            continue
            
        if is_mislabeled_file:
            if 'januari sd mei 2025' in h_norm:
                col_mapping['curr_month'] = idx
                continue
            elif '2025 audited' in h_norm or h_norm == '2025':
                col_mapping['prev_year_yoy'] = idx
                col_mapping['Desember 2025'] = idx
                continue
            elif '2024' in h_norm:
                col_mapping['Desember 2024'] = idx
                col_mapping['prev_year_2024'] = idx
                continue
            
        # Check standard categories first
        if any(x in h_norm for x in ['mei 2026', '2026-05-01', 'mei-26', 'may-26', '31 mei 2026', 'kinerja mei 2026']):
            is_ytd = 's/d' in h_norm or 's.d' in h_norm or 'ytd' in h_norm or 'kumulatif' in h_norm or 'per ' in h_norm or h_norm.startswith('per') or 'audited' in h_norm
            if 'curr_month' in col_mapping:
                prev_idx = col_mapping['curr_month']
                prev_h_norm = normalize_string(df_headers[prev_idx])
                prev_is_ytd = 's/d' in prev_h_norm or 's.d' in prev_h_norm or 'ytd' in prev_h_norm or 'kumulatif' in prev_h_norm or 'per ' in prev_h_norm or prev_h_norm.startswith('per') or 'audited' in prev_h_norm
                if is_ytd and not prev_is_ytd:
                    col_mapping['curr_month'] = idx
            else:
                col_mapping['curr_month'] = idx
        elif any(x in h_norm for x in ['april 2026', '2026-04-01', 'apr-26', '30 april 2026']):
            is_ytd = 's/d' in h_norm or 's.d' in h_norm or 'ytd' in h_norm or 'kumulatif' in h_norm or 'per ' in h_norm or h_norm.startswith('per') or 'audited' in h_norm
            if 'prev_month' in col_mapping:
                prev_idx = col_mapping['prev_month']
                prev_h_norm = normalize_string(df_headers[prev_idx])
                prev_is_ytd = 's/d' in prev_h_norm or 's.d' in prev_h_norm or 'ytd' in prev_h_norm or 'kumulatif' in prev_h_norm or 'per ' in prev_h_norm or prev_h_norm.startswith('per') or 'audited' in prev_h_norm
                if is_ytd and not prev_is_ytd:
                    col_mapping['prev_month'] = idx
            else:
                col_mapping['prev_month'] = idx
        elif any(x in h_norm for x in ['mei 2025', '2025-05-01', 'mei-25', 'may-25', '31 mei 2025']):
            is_ytd = 's/d' in h_norm or 's.d' in h_norm or 'ytd' in h_norm or 'kumulatif' in h_norm or 'per ' in h_norm or h_norm.startswith('per') or 'audited' in h_norm
            if 'yoy_prev' in col_mapping:
                prev_idx = col_mapping['yoy_prev']
                prev_h_norm = normalize_string(df_headers[prev_idx])
                prev_is_ytd = 's/d' in prev_h_norm or 's.d' in prev_h_norm or 'ytd' in prev_h_norm or 'kumulatif' in prev_h_norm or 'per ' in prev_h_norm or prev_h_norm.startswith('per') or 'audited' in prev_h_norm
                if is_ytd and not prev_is_ytd:
                    col_mapping['yoy_prev'] = idx
                    col_mapping['prev_year_yoy'] = idx
            else:
                col_mapping['yoy_prev'] = idx
                col_mapping['prev_year_yoy'] = idx
        elif any(x in h_norm for x in ['des 25', 'desember 2025', '2025-12-31', 'audited', '31 des 25']):
            if 'prev_year_yoy' not in col_mapping:
                col_mapping['prev_year_yoy'] = idx
        elif any(x in h_norm for x in ['anggaran 2026', 'rkap 2026', 'rkap fy', 'rkap tahunan']):
            col_mapping['rkap_fy'] = idx
        elif any(x in h_norm for x in ['rkap ytd', 'target ytd', 'ytd rkap']):
            col_mapping['rkap_ytd'] = idx
            
        # Parse month/year for time series
        date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', h_norm)
        if date_match:
            year = date_match.group(1)
            month_num = int(date_match.group(2))
            key_name = f"{months_id[month_num]} {year}"
            is_ytd = any(x in h_norm for x in ['ytd', 's/d', 's.d', 'kumulatif', 'audited', 'sd'])
            if key_name in col_mapping:
                prev_idx = col_mapping[key_name]
                prev_h_norm = normalize_string(df_headers[prev_idx])
                prev_is_ytd = any(x in prev_h_norm for x in ['ytd', 's/d', 's.d', 'kumulatif', 'audited', 'sd'])
                if prev_is_ytd and not is_ytd:
                    col_mapping[key_name] = idx
            else:
                col_mapping[key_name] = idx
        else:
            for m_num, m_name in enumerate(months_id[1:], 1):
                m_lower = m_name.lower()
                if m_lower[:3] in h_norm or m_lower in h_norm:
                    year_match = re.search(r'20\d{2}', h_norm)
                    year_str = year_match.group(0) if year_match else "2026"
                    key_name = f"{m_name} {year_str}"
                    is_ytd = any(x in h_norm for x in ['ytd', 's/d', 's.d', 'kumulatif', 'audited', 'sd'])
                    if key_name in col_mapping:
                        prev_idx = col_mapping[key_name]
                        prev_h_norm = normalize_string(df_headers[prev_idx])
                        prev_is_ytd = any(x in prev_h_norm for x in ['ytd', 's/d', 's.d', 'kumulatif', 'audited', 'sd'])
                        if prev_is_ytd and not is_ytd:
                            col_mapping[key_name] = idx
                    else:
                        col_mapping[key_name] = idx
                    break
                    
    # Fallbacks
    if 'curr_month' not in col_mapping:
        for idx, h in enumerate(df_headers):
            h_norm = normalize_string(h)
            if any(x in h_norm for x in ['%', 'rasio', 'ratio', 'persen']):
                continue
            if any(x in h_norm for x in ['rkap', 'anggaran', 'target', 'plan']):
                continue
            if '2026-05-01' in h_norm or h_norm == 'mei-26' or h_norm == 'may-26':
                col_mapping['curr_month'] = idx
    if 'prev_month' not in col_mapping:
        for idx, h in enumerate(df_headers):
            h_norm = normalize_string(h)
            if any(x in h_norm for x in ['%', 'rasio', 'ratio', 'persen']):
                continue
            if any(x in h_norm for x in ['rkap', 'anggaran', 'target', 'plan']):
                continue
            if '2026-04-01' in h_norm or h_norm == 'apr-26':
                col_mapping['prev_month'] = idx

    # Generic fallback: if no explicitly recognized current-period column was found,
    # anchor curr/prev/yoy to the latest month-year column detected above so files
    # from other reporting cycles work without hardcoded period labels.
    # 'Desember' is excluded as an anchor because December columns in these
    # workbooks are audited prior-year balances, not the reporting month.
    if 'curr_month' not in col_mapping:
        month_candidates = []
        for key in col_mapping:
            m = re.match(r'^(Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November) (20\d{2})$', str(key))
            if m:
                month_candidates.append((int(m.group(2)), months_id.index(m.group(1)), key))
        if month_candidates:
            month_candidates.sort()
            year, m_num, key = month_candidates[-1]
            col_mapping['curr_month'] = col_mapping[key]
            prev_key = f"{months_id[m_num - 1]} {year}" if m_num > 1 else f"Desember {year - 1}"
            yoy_key = f"{months_id[m_num]} {year - 1}"
            if prev_key in col_mapping:
                col_mapping.setdefault('prev_month', col_mapping[prev_key])
            if yoy_key in col_mapping:
                col_mapping.setdefault('yoy_prev', col_mapping[yoy_key])
                col_mapping.setdefault('prev_year_yoy', col_mapping[yoy_key])

    return col_mapping

def parse_unified_bs_sheet(df):
    desc_col_idx = None
    header_row_idx = None
    for idx, row in df.iterrows():
        row_vals = [str(x).lower().strip() for x in row.values]
        for col_i, val in enumerate(row_vals):
            if val in ['keterangan', 'uraian', 'pos neraca', 'neraca']:
                header_row_idx = idx
                desc_col_idx = col_i
                break
        if header_row_idx is not None:
            break
            
    if header_row_idx is None:
        for idx, row in df.iterrows():
            row_vals = [str(x).lower().strip() for x in row.values]
            if any('kas dan bank' in x or 'kas dan giro bank' in x for x in row_vals):
                header_row_idx = max(0, idx - 1)
                desc_col_idx = [i for i, x in enumerate(row_vals) if 'kas' in x][0]
                break
                
    if header_row_idx is None:
        return {}
        
    num_cols = df.shape[1]
    # Check if the row below is also part of the header (double-row header)
    is_double_header = False
    if header_row_idx is not None and header_row_idx + 1 < len(df):
        row_below = df.iloc[header_row_idx + 1]
        desc_val_below = row_below.iloc[desc_col_idx]
        desc_empty = pd.isna(desc_val_below) or str(desc_val_below).strip() == "" or str(desc_val_below).strip().lower() == 'nan'
        
        string_keywords = 0
        numeric_values = 0
        for col_i, val in enumerate(row_below.values):
            if col_i == desc_col_idx:
                continue
            if pd.isna(val):
                continue
            val_str = str(val).lower().strip()
            if val_str == "" or val_str == 'nan':
                continue
            try:
                float(val)
                numeric_values += 1
            except (ValueError, TypeError):
                string_keywords += 1
                
        if desc_empty and string_keywords > 0 and numeric_values == 0:
            is_double_header = True

    if is_double_header:
        row_top = df.iloc[header_row_idx].tolist()
        row_sub = df.iloc[header_row_idx + 1].tolist()
        data_start_idx = header_row_idx + 2
    else:
        has_top_header = False
        if header_row_idx > 0:
            row_above = [str(x).lower().strip() for x in df.iloc[header_row_idx - 1].values]
            if any(any(k in x for k in ['update', 'realisasi', 'anggaran', 'rkap', 'audited', '2025', '2026']) for x in row_above if x != 'nan'):
                has_top_header = True
                
        if has_top_header:
            row_top = df.iloc[header_row_idx - 1].tolist()
            row_sub = df.iloc[header_row_idx].tolist()
            data_start_idx = header_row_idx + 1
        else:
            row_top = df.iloc[header_row_idx].tolist()
            row_sub = df.iloc[header_row_idx].tolist()
            data_start_idx = header_row_idx + 1
        
    unique_headers = _generate_combined_headers(row_top, row_sub)
    col_mapping = classify_columns(unique_headers)
    
    if not col_mapping:
        return {}
        
    bs_data = {}
    section = 'lancar'
    
    for r_idx in range(data_start_idx, len(df)):
        raw_label = df.iloc[r_idx, desc_col_idx]
        if pd.isna(raw_label) or str(raw_label).strip() == "":
            continue
            
        label_norm = normalize_string(raw_label)
        
        # Section detection
        if any(x in label_norm for x in ['aset tidak lancar', 'aset non lancar', 'aset non-lancar']) and not any(y in label_norm for y in ['jumlah', 'total', 'subtotal']):
            section = 'tidak_lancar'
            continue
        elif any(x in label_norm for x in ['liabilitas dan ekuitas', 'kewajiban dan ekuitas', 'kewajiban dan modal']) and not any(y in label_norm for y in ['jumlah', 'total', 'subtotal']):
            section = 'liabilitas_lancar'
            continue
        elif any(x in label_norm for x in ['liabilitas lancar', 'kewajiban lancar', 'liabilitas jangka pendek']) and not any(y in label_norm for y in ['jumlah', 'total', 'subtotal']):
            section = 'liabilitas_lancar'
            continue
        elif any(x in label_norm for x in ['liabilitas tidak lancar', 'kewajiban tidak lancar', 'liabilitas jangka panjang']) and not any(y in label_norm for y in ['jumlah', 'total', 'subtotal']):
            section = 'liabilitas_tidak_lancar'
            continue
        elif any(x == label_norm for x in ['ekuitas', 'modal sendiri', 'modal']) or (any(x in label_norm for x in ['ekuitas', 'modal sendiri']) and not any(y in label_norm for y in ['jumlah', 'total', 'subtotal'])):
            section = 'ekuitas'
            continue
            
        mapped_key = None
        
        if 'kas dan bank' in label_norm or 'kas dan giro bank' in label_norm or 'kas dan setara kas' in label_norm:
            mapped_key = 'cash_and_bank'
        elif 'deposito' in label_norm or 'deposito pada bank' in label_norm or 'deposito berjangka mudharabah' in label_norm:
            if 'tujuan tertentu' in label_norm or 'dibatasi' in label_norm or section == 'tidak_lancar':
                mapped_key = 'deposito_tidak_lancar'
            else:
                mapped_key = 'deposito_lancar'
        elif 'surat berharga syariah negara' in label_norm or 'sbsn' in label_norm or 'obligasi pemerintah' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'sbsn_tidak_lancar'
            else:
                mapped_key = 'sbsn_lancar'
        elif 'sukuk korporasi' in label_norm or 'obligasi korporasi' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'sukuk_korporasi_tidak_lancar'
            else:
                mapped_key = 'sukuk_korporasi_lancar'
        elif 'reksa dana syariah' in label_norm or 'reksadana' in label_norm or 'investasi - reksadana' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'reksadana_tidak_lancar'
            else:
                mapped_key = 'reksadana_lancar'
        elif 'surat berharga syariah yang diterbitkan oleh bank indonesia' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'sbsn_bi_tidak_lancar'
            else:
                mapped_key = 'sbsn_bi_lancar'
        elif 'saham' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'saham_tidak_lancar'
            else:
                mapped_key = 'saham_lancar'
        elif 'efek beragun aset syariah' in label_norm or 'ebas' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'ebas_tidak_lancar'
            else:
                mapped_key = 'ebas_lancar'
        elif 'medium term notes' in label_norm or 'mtn' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'mtn_tidak_lancar'
            else:
                mapped_key = 'mtn_lancar'
        elif 'repurchase agreement' in label_norm or 'repo' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'repo_tidak_lancar'
            else:
                mapped_key = 'repo_lancar'
        elif 'real estat syariah' in label_norm or 'dires' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'dires_tidak_lancar'
            else:
                mapped_key = 'dires_lancar'
        elif 'tanah dan bangunan' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'tanah_bangunan_tidak_lancar'
            else:
                mapped_key = 'tanah_bangunan_lancar'
        elif 'penyertaan langsung' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'penyertaan_langsung_tidak_lancar'
            else:
                mapped_key = 'penyertaan_langsung_lancar'
        elif 'obligasi daerah' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'obligasi_daerah_tidak_lancar'
            else:
                mapped_key = 'obligasi_daerah_lancar'
        elif 'infrastruktur' in label_norm or 'dinfra' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'dinfra_tidak_lancar'
            else:
                mapped_key = 'dinfra_lancar'
        elif 'lainnya' in label_norm and 'aset' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'lainnya_tidak_lancar'
            else:
                mapped_key = 'lainnya_lancar'
        elif 'piutang imbal jasa kafalah' in label_norm or 'piutang ijk' in label_norm:
            if 'ckpn' in label_norm:
                if section == 'tidak_lancar':
                    mapped_key = 'piutang_ijk_ckpn_tidak_lancar'
                else:
                    mapped_key = 'piutang_ijk_ckpn_lancar'
            else:
                if section == 'tidak_lancar':
                    mapped_key = 'piutang_ijk_tidak_lancar'
                else:
                    mapped_key = 'piutang_ijk_lancar'
        elif 'piutang penjaminan bersama' in label_norm or 'piutang co-guarantee' in label_norm or 'piutang co-guar' in label_norm or 'piutang tawidh' in label_norm or "piutang ta'widh" in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'piutang_co_guarantee_tidak_lancar'
            else:
                mapped_key = 'piutang_co_guarantee_lancar'
        elif 'piutang reasuransi syariah/penjaminan ulang syariah' in label_norm or 'piutang reasuransi' in label_norm or 'piutang reas' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'piutang_reasuransi_tidak_lancar'
            else:
                mapped_key = 'piutang_reasuransi_lancar'
        elif 'pendapatan yang masih harus diterima' in label_norm or 'piutang investasi' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'pendapatan_ymhd_tidak_lancar'
            else:
                mapped_key = 'pendapatan_ymhd_lancar'
        elif 'beban dibayar di muka' in label_norm or 'biaya dibayar dimuka' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'beban_dibayar_dimuka_tidak_lancar'
            else:
                mapped_key = 'beban_dibayar_dimuka_lancar'
        elif 'biaya akuisisi yang ditangguhkan' in label_norm:
            mapped_key = 'deferred_acquisition_cost'
        elif 'aset reas' in label_norm or 'aset reasuransi' in label_norm:
            mapped_key = 'reinsurance_assets'
        elif 'piutang dalam rangka restrukturisasi penjaminan' in label_norm or 'restrukturisasi' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'piutang_restrukturisasi_tidak_lancar'
            else:
                mapped_key = 'piutang_restrukturisasi_lancar'
        elif 'aset pajak tangguhan' in label_norm:
            mapped_key = 'deferred_tax_assets'
        elif 'aset tetap' in label_norm:
            mapped_key = 'fixed_assets'
        elif 'aset tidak berwujud' in label_norm:
            mapped_key = 'intangible_assets'
        elif 'aset lain-lain' in label_norm or 'aset lain' in label_norm:
            if section == 'tidak_lancar':
                mapped_key = 'other_assets_tidak_lancar'
            else:
                mapped_key = 'other_assets_lancar'
        elif 'jumlah aset lancar' in label_norm:
            mapped_key = 'total_current_assets'
        elif 'jumlah aset tidak lancar' in label_norm:
            mapped_key = 'total_noncurrent_assets'
        elif 'jumlah aset' in label_norm or label_norm == 'aset' or label_norm == 'total aset':
            mapped_key = 'total_assets'
        elif 'utang klaim co-guarantee' in label_norm or 'utang klaim co-guar' in label_norm:
            if section == 'liabilitas_tidak_lancar':
                mapped_key = 'co_guarantee_claims_payable_tidak_lancar'
            else:
                mapped_key = 'co_guarantee_claims_payable'
        elif 'utang ijk co-guarantee' in label_norm or 'pendapatan diterima dimuka' in label_norm:
            if section == 'liabilitas_tidak_lancar':
                mapped_key = 'co_guarantee_ijk_payable_tidak_lancar'
            else:
                mapped_key = 'co_guarantee_ijk_payable'
        elif 'utang kontribusi reasuransi' in label_norm or 'penjaminan ulang' in label_norm or 'utang reas' in label_norm:
            if section == 'liabilitas_tidak_lancar':
                mapped_key = 'reinsurance_payable_tidak_lancar'
            else:
                mapped_key = 'reinsurance_payable'
        elif 'utang klaim' in label_norm or 'utang tawidh' in label_norm:
            if section == 'liabilitas_tidak_lancar':
                mapped_key = 'claims_payable_tidak_lancar'
            else:
                mapped_key = 'claims_payable_lancar'
        elif 'cadangan klaim' in label_norm or 'estimasi tawidh retensi sendiri' in label_norm or 'estimasi ta\'widh retensi sendiri' in label_norm:
            mapped_key = 'claims_reserve'
        elif 'penampungan ijk' in label_norm:
            if section == 'liabilitas_tidak_lancar':
                mapped_key = 'ijk_suspense_tidak_lancar'
            else:
                mapped_key = 'ijk_suspense_lancar'
        elif 'ijk ditangguhkan' in label_norm or 'ujrah yang belum merupakan pendapatan' in label_norm or 'cad. premi' in label_norm or 'ijk ybmp' in label_norm:
            if section == 'liabilitas_tidak_lancar':
                mapped_key = 'deferred_ijk_tidak_lancar'
            else:
                mapped_key = 'deferred_ijk_lancar'
        elif 'utang komisi' in label_norm or 'biaya komisi' in label_norm:
            if section == 'liabilitas_tidak_lancar':
                mapped_key = 'commission_payable_tidak_lancar'
            else:
                mapped_key = 'commission_payable'
        elif 'beban yang masih harus dibayar' in label_norm or 'beban akrual' in label_norm:
            mapped_key = 'accrued_expenses'
        elif 'utang pajak' in label_norm:
            mapped_key = 'tax_payable'
        elif 'utang zakat' in label_norm:
            mapped_key = 'zakat_payable'
        elif 'imbalan kerja jangka panjang' in label_norm or 'imbalan pasca kerja' in label_norm:
            mapped_key = 'employee_benefits_tidak_lancar'
        elif 'liabilitas lain-lain' in label_norm or 'utang lain-lain' in label_norm or 'liabilitas lain' in label_norm:
            if section == 'liabilitas_tidak_lancar':
                mapped_key = 'other_liabilities_tidak_lancar'
            else:
                mapped_key = 'other_liabilities_lancar'
        elif 'jumlah liabilitas lancar' in label_norm:
            mapped_key = 'total_current_liabilities'
        elif 'jumlah liabilitas tidak lancar' in label_norm:
            mapped_key = 'total_noncurrent_liabilities'
        elif ('jumlah liabilitas' in label_norm or label_norm == 'liabilitas' or label_norm == 'total liabilitas') and 'ekuitas' not in label_norm and 'modal' not in label_norm:
            mapped_key = 'total_liabilities'
        elif 'modal ditempatkan dan disetor' in label_norm or 'modal disetor' in label_norm or 'modal kerja' in label_norm or (label_norm == 'modal' and section == 'ekuitas'):
            mapped_key = 'capital'
        elif 'setoran modal diterima di muka' in label_norm:
            mapped_key = 'paid_in_capital_prepaid'
        elif 'cadangan umum' in label_norm:
            mapped_key = 'general_reserve'
        elif 'laba (rugi) tahun berjalan' in label_norm or 'laba tahun berjalan' in label_norm or (label_norm == 'laba setelah pajak' and section == 'ekuitas'):
            mapped_key = 'current_year_profit'
        elif 'saldo laba' in label_norm:
            mapped_key = 'retained_earnings'
        elif 'deviden' in label_norm:
            mapped_key = 'dividends'
        elif 'komponen ekuitas lainnya' in label_norm or 'ekuitas lainnya' in label_norm:
            mapped_key = 'other_equity_components'
        elif ('jumlah ekuitas' in label_norm or label_norm == 'ekuitas' or label_norm == 'total ekuitas') and 'liabilitas' not in label_norm:
            mapped_key = 'total_equity'

        # Fallback: fuzzy synonym matching for labels the exact chain missed
        if mapped_key is None:
            mapped_key = _fuzzy_match_account(label_norm, BS_FALLBACK_SYNONYMS)

        if mapped_key:
            if mapped_key not in bs_data:
                bs_data[mapped_key] = {}
            for category, col_idx in col_mapping.items():
                val = clean_value(df.iloc[r_idx, col_idx])
                bs_data[mapped_key][category] = val
                
    # Normalize scale if represented in millions
    if 'total_assets' in bs_data:
        vals = [v for v in bs_data['total_assets'].values() if pd.notna(v) and v != 0.0]
        if vals:
            max_val = max(abs(v) for v in vals)
            if max_val < 10_000_000.0:  # Scaled in Millions
                for k in bs_data:
                    for c in bs_data[k]:
                        if pd.notna(bs_data[k][c]):
                            bs_data[k][c] *= 1_000_000.0
                    
    # Dynamic current subtotal fallbacks
    non_current_asset_keys = [
        'fixed_assets', 'intangible_assets', 'deferred_tax_assets', 
        'deposito_tidak_lancar', 'sbsn_tidak_lancar', 'reksadana_tidak_lancar', 
        'saham_tidak_lancar', 'sukuk_korporasi_tidak_lancar', 'mtn_tidak_lancar', 
        'other_assets_tidak_lancar'
    ]
    non_current_liability_keys = [
        'employee_benefits_tidak_lancar', 'other_liabilities_tidak_lancar',
        'co_guarantee_claims_payable_tidak_lancar', 'co_guarantee_ijk_payable_tidak_lancar',
        'reinsurance_payable_tidak_lancar', 'claims_payable_tidak_lancar',
        'ijk_suspense_tidak_lancar', 'deferred_ijk_tidak_lancar',
        'commission_payable_tidak_lancar'
    ]

    # 1. Fallback for total_current_assets
    if 'total_current_assets' not in bs_data:
        bs_data['total_current_assets'] = {}
    
    total_assets_dict = bs_data.get('total_assets', {})
    for col in total_assets_dict:
        curr_assets_val = bs_data['total_current_assets'].get(col, 0.0)
        if curr_assets_val == 0.0:
            total_assets_val = total_assets_dict.get(col, 0.0)
            non_current_val = sum(bs_data.get(k, {}).get(col, 0.0) for k in non_current_asset_keys)
            bs_data['total_current_assets'][col] = max(total_assets_val - non_current_val, 0.0)
            
    # 2. Fallback for total_current_liabilities
    if 'total_current_liabilities' not in bs_data:
        bs_data['total_current_liabilities'] = {}
        
    total_liab_dict = bs_data.get('total_liabilities', {})
    for col in total_liab_dict:
        curr_liab_val = bs_data['total_current_liabilities'].get(col, 0.0)
        if curr_liab_val == 0.0:
            total_liab_val = total_liab_dict.get(col, 0.0)
            non_current_val = sum(bs_data.get(k, {}).get(col, 0.0) for k in non_current_liability_keys)
            bs_data['total_current_liabilities'][col] = max(total_liab_val - non_current_val, 0.0)
            
    return bs_data

def parse_unified_pl_sheet(df):
    desc_col_idx = None
    header_row_idx = None
    for idx, row in df.iterrows():
        row_vals = [str(x).lower().strip() for x in row.values]
        for col_i, val in enumerate(row_vals):
            if val in ['keterangan', 'uraian', 'pos laba rugi', 'laba rugi']:
                header_row_idx = idx
                desc_col_idx = col_i
                break
        if header_row_idx is not None:
            break
            
    if header_row_idx is None:
        for idx, row in df.iterrows():
            row_vals = [str(x).lower().strip() for x in row.values]
            if any('imbal jasa kafalah' in x or 'pendapatan penjaminan' in x for x in row_vals):
                header_row_idx = max(0, idx - 1)
                desc_col_idx = [i for i, x in enumerate(row_vals) if 'imbal' in x or 'pendapatan' in x][0]
                break
                
    if header_row_idx is None:
        return {}
        
    num_cols = df.shape[1]
    # Check if the row below is also part of the header (double-row header)
    is_double_header = False
    if header_row_idx is not None and header_row_idx + 1 < len(df):
        row_below = df.iloc[header_row_idx + 1]
        desc_val_below = row_below.iloc[desc_col_idx]
        desc_empty = pd.isna(desc_val_below) or str(desc_val_below).strip() == "" or str(desc_val_below).strip().lower() == 'nan'
        
        string_keywords = 0
        numeric_values = 0
        for col_i, val in enumerate(row_below.values):
            if col_i == desc_col_idx:
                continue
            if pd.isna(val):
                continue
            val_str = str(val).lower().strip()
            if val_str == "" or val_str == 'nan':
                continue
            try:
                float(val)
                numeric_values += 1
            except (ValueError, TypeError):
                string_keywords += 1
                
        if desc_empty and string_keywords > 0 and numeric_values == 0:
            is_double_header = True

    if is_double_header:
        row_top = df.iloc[header_row_idx].tolist()
        row_sub = df.iloc[header_row_idx + 1].tolist()
        data_start_idx = header_row_idx + 2
    else:
        has_top_header = False
        if header_row_idx > 0:
            row_above = [str(x).lower().strip() for x in df.iloc[header_row_idx - 1].values]
            if any(any(k in x for k in ['update', 'realisasi', 'anggaran', 'rkap', 'audited', '2025', '2026']) for x in row_above if x != 'nan'):
                has_top_header = True
                
        if has_top_header:
            row_top = df.iloc[header_row_idx - 1].tolist()
            row_sub = df.iloc[header_row_idx].tolist()
            data_start_idx = header_row_idx + 1
        else:
            row_top = df.iloc[header_row_idx].tolist()
            row_sub = df.iloc[header_row_idx].tolist()
            data_start_idx = header_row_idx + 1
        
    unique_headers = _generate_combined_headers(row_top, row_sub)
    col_mapping = classify_columns(unique_headers)
    
    if not col_mapping:
        return {}
        
    pl_data = {}
    
    for r_idx in range(data_start_idx, len(df)):
        raw_label = df.iloc[r_idx, desc_col_idx]
        if pd.isna(raw_label) or str(raw_label).strip() == "":
            continue
            
        label_norm = normalize_string(raw_label)
        mapped_key = None
        
        if 'imbal jasa kafalah bruto' in label_norm or 'pendapatan jasa penjaminan (ijk)' in label_norm or 'pendapatan penjaminan bruto' in label_norm:
            mapped_key = 'ijk_revenue'
        elif 'beban penjaminan ulang' in label_norm or 'beban penjaminan reas' in label_norm:
            mapped_key = 'reinsurance_expense'
        elif 'komisi penjaminan ulang' in label_norm:
            mapped_key = 'reinsurance_commission'
        elif 'kenaikan' in label_norm and 'yang belum merupakan pendapatan' in label_norm:
            mapped_key = 'change_unearned_ijk'
        elif 'penurunan' in label_norm and 'yang belum merupakan pendapatan' in label_norm:
            mapped_key = 'change_unearned_ijk'
        elif 'penurunan (kenaikan) ijk ybmp' in label_norm or 'kenaikan (penurunan) ijk ybmp' in label_norm:
            mapped_key = 'change_unearned_ijk'
        elif 'pendapatan underwriting lain' in label_norm or 'penerimaan kafalah lain' in label_norm:
            mapped_key = 'other_underwriting_revenue'
        elif 'pendapatan underwriting' in label_norm or 'jumlah pendapatan kafalah' in label_norm:
            mapped_key = 'net_underwriting_revenue'
        elif 'ta\'widh bruto' in label_norm or 'tawidh bruto' in label_norm or label_norm == 'ta\'widh' or label_norm == 'tawidh' or label_norm == 'beban klaim':
            mapped_key = 'gross_claims'
        elif 'ta\'widh reasuransi' in label_norm or 'tawidh reas' in label_norm:
            mapped_key = 'reinsurance_claims'
        elif 'estimasi tawidh retensi sendiri' in label_norm or 'estimasi ta\'widh retensi sendiri' in label_norm or 'etrs' in label_norm:
            mapped_key = 'change_claims_retention'
        elif 'penerimaan recoveries' in label_norm or 'recoveries' in label_norm or 'pendapatan recoveries' in label_norm or 'subrogasi' in label_norm:
            mapped_key = 'net_recoveries'
        elif 'beban komisi' in label_norm or 'biaya komisi' in label_norm or 'beban akuisisi' in label_norm:
            mapped_key = 'commission_expense'
        elif 'beban underwriting lain' in label_norm or 'beban kafalah lain' in label_norm:
            mapped_key = 'other_underwriting_expense'
        elif 'hasil underwriting neto' in label_norm or 'pendapatan kafalah bersih' in label_norm:
            mapped_key = 'net_underwriting_result'
        elif 'hasil investasi' in label_norm or 'nisbah (bagi hasil) investasi' in label_norm:
            mapped_key = 'investment_income'
        elif 'beban usaha' in label_norm or 'jumlah beban usaha' in label_norm or 'total beban usaha' in label_norm:
            mapped_key = 'total_operating_expense'
        elif 'laba usaha' in label_norm or 'laba (rugi) usaha' in label_norm:
            mapped_key = 'operating_profit'
        elif 'laba sebelum pajak' in label_norm:
            mapped_key = 'pretax_profit'
        elif 'laba setelah pajak' in label_norm or 'laba tahun berjalan' in label_norm:
            mapped_key = 'net_profit'

        # Fallback: fuzzy synonym matching for labels the exact chain missed
        if mapped_key is None:
            mapped_key = _fuzzy_match_account(label_norm, PL_FALLBACK_SYNONYMS)

        if mapped_key:
            if mapped_key not in pl_data:
                pl_data[mapped_key] = {}
            # Commission lines come in main + 'tangguhan' (deferred) sub-rows that must be
            # summed, not overwritten (e.g. Komisi penjaminan ulang + ...tangguhan).
            accumulate = mapped_key in ('reinsurance_commission', 'commission_expense')
            for category, col_idx in col_mapping.items():
                val = clean_value(df.iloc[r_idx, col_idx])
                if accumulate and category in pl_data[mapped_key]:
                    pl_data[mapped_key][category] += val
                else:
                    pl_data[mapped_key][category] = val

    # Normalize scale if represented in millions
    if 'ijk_revenue' in pl_data:
        vals = [v for v in pl_data['ijk_revenue'].values() if pd.notna(v) and v != 0.0]
        if vals:
            max_val = max(abs(v) for v in vals)
            if max_val < 10_000_000.0:  # Scaled in Millions
                for k in pl_data:
                    for c in pl_data[k]:
                        if pd.notna(pl_data[k][c]):
                            pl_data[k][c] *= 1_000_000.0
                    
    return pl_data

def parse_excel_file(file_path):
    """
    Main entry point for parsing any uploaded JPAS Excel file.
    Dinamis memindai seluruh sheet, mengklasifikasi tipe sheet,
    dan menggabungkan hasil parsing secara akumulatif.
    """
    import pandas as pd
    from utils import classify_sheet_by_content
    
    xl = pd.ExcelFile(file_path)
    sheets = xl.sheet_names
    
    result = {
        'pl_data': {},
        'bs_data': {},
        'gr_data': {},
        'mitra_data': [],
        'cfs_data': None,
        'huw_data': None,
        'pl_df': None,
        'bs_df': None
    }
    
    sheet_candidates = {
        'BS': [],
        'PL': [],
        'CF': [],
        'HUW': [],
        'Gearing': [],
        'Mitra': []
    }
    
    for sheet in sheets:
        try:
            df = xl.parse(sheet, header=None)
            stypes = classify_sheet_by_content(df)
            if isinstance(stypes, str):
                stypes = [stypes]
            
            for stype in stypes:
                if stype == 'BS':
                    bs_parsed = parse_unified_bs_sheet(df)
                    total_points = sum(1 for k, v in bs_parsed.items() for col, val in v.items() if val != 0.0)
                    score_curr = sum(1 for k, v in bs_parsed.items() if v.get('curr_month', 0.0) != 0.0)
                    df_clean = parse_full_statement_sheet(xl, sheet)
                    sheet_candidates['BS'].append((sheet, (total_points, score_curr), bs_parsed, df_clean))
                elif stype == 'PL':
                    pl_parsed = parse_unified_pl_sheet(df)
                    total_points = sum(1 for k, v in pl_parsed.items() for col, val in v.items() if val != 0.0)
                    score_curr = sum(1 for k, v in pl_parsed.items() if v.get('curr_month', 0.0) != 0.0)
                    df_clean = parse_full_statement_sheet(xl, sheet)
                    sheet_candidates['PL'].append((sheet, (total_points, score_curr), pl_parsed, df_clean))
                elif stype == 'CF':
                    cfs_parsed = parse_cash_flow_sheet(xl, sheet_name=sheet)
                    score = len(cfs_parsed) if cfs_parsed is not None else 0
                    sheet_candidates['CF'].append((sheet, (score, 0), cfs_parsed))
                elif stype == 'HUW':
                    huw_parsed = parse_huw_sheet(xl, sheet_name=sheet)
                    score = len(huw_parsed) if huw_parsed is not None else 0
                    sheet_candidates['HUW'].append((sheet, (score, 0), huw_parsed))
                elif stype == 'Gearing':
                    gr_parsed = {}
                    df_gr = xl.parse(sheet)
                    os_net = 0.0
                    equity = 0.0
                    gearing = 0.0
                    for idx, row in df_gr.iterrows():
                        label = str(row.iloc[1]).strip().lower()
                        if 'nilai penjaminan' in label or 'ditanggung sendiri' in label:
                            os_net = clean_value(row.iloc[5])
                            if os_net > 1_000_000_000:
                                os_net /= 1_000_000
                        elif 'modal sendiri' in label or 'ekuitas' in label:
                            equity = clean_value(row.iloc[5])
                            if equity > 1_000_000_000:
                                equity /= 1_000_000
                        elif 'gearing ratio' in label:
                            gearing = clean_value(row.iloc[5])
                    gr_parsed = {
                        'os_net': os_net,
                        'equity': equity,
                        'gearing_ratio': gearing if gearing > 0 else (os_net / equity if equity > 0 else 0)
                    }
                    score = 1 if os_net > 0 or equity > 0 else 0
                    sheet_candidates['Gearing'].append((sheet, (score, 0), gr_parsed))
                elif stype == 'Mitra':
                    mitra_parsed = parse_rekapan_kafalah(file_path, sheet_name=sheet)
                    score = len(mitra_parsed.get('mitra_data', []))
                    sheet_candidates['Mitra'].append((sheet, (score, 0), mitra_parsed))
        except Exception:
            logger.exception("Error parsing sheet '%s' in file '%s'", sheet, os.path.basename(file_path))
            continue
            
    # Select the most complete candidate sheet for each type
    if sheet_candidates['BS']:
        # Prefer sheets that actually have current-month data (score_curr>0); a sheet
        # with many columns but zero current data is useless. Then by completeness.
        best_bs = sorted(sheet_candidates['BS'], key=lambda x: (x[1][1] > 0, x[1][0], x[1][1], 'kinerja' in x[0].lower(), 'summary' in x[0].lower()), reverse=True)[0]
        result['bs_data'] = best_bs[2]
        if best_bs[3] is not None:
            result['bs_df'] = filter_statement_columns(best_bs[3])

    if sheet_candidates['PL']:
        # Prefer sheets that actually have current-month data (score_curr>0); a sheet
        # with many columns but zero current data is useless. Then by completeness.
        best_pl = sorted(sheet_candidates['PL'], key=lambda x: (x[1][1] > 0, x[1][0], x[1][1], 'kinerja' in x[0].lower(), 'summary' in x[0].lower()), reverse=True)[0]
        result['pl_data'] = best_pl[2]
        if best_pl[3] is not None:
            result['pl_df'] = filter_statement_columns(best_pl[3])
            
    if sheet_candidates['Gearing']:
        best_gr = sorted(sheet_candidates['Gearing'], key=lambda x: x[1], reverse=True)[0]
        result['gr_data'] = best_gr[2]
        
    if sheet_candidates['CF']:
        best_cf = sorted(sheet_candidates['CF'], key=lambda x: x[1], reverse=True)[0]
        result['cfs_data'] = best_cf[2]
        
    if sheet_candidates['HUW']:
        best_huw = sorted(sheet_candidates['HUW'], key=lambda x: x[1], reverse=True)[0]
        result['huw_data'] = best_huw[2]
        
    if sheet_candidates['Mitra']:
        best_mit = sorted(sheet_candidates['Mitra'], key=lambda x: x[1], reverse=True)[0]
        if best_mit[2].get('mitra_data'):
            result['mitra_data'] = best_mit[2]['mitra_data']
            
    if not result['gr_data'] and result['bs_data']:
        eq_val = result['bs_data'].get('total_equity', {}).get('curr_month', 0.0)
        eq_val_m = eq_val / 1_000_000.0 if eq_val > 0 else 0.0
        result['gr_data'] = {
            'os_net': 0.0,
            'equity': eq_val_m,
            'gearing_ratio': 0.0
        }
        
    # Ensure backward compatibility for app.py UI
    bs = result['bs_data']
    if bs:
        # sbsn_invest
        if 'sbsn_invest' not in bs:
            bs['sbsn_invest'] = {}
            for col in set(list(bs.get('sbsn_lancar', {}).keys()) + list(bs.get('sbsn_tidak_lancar', {}).keys())):
                bs['sbsn_invest'][col] = bs.get('sbsn_lancar', {}).get(col, 0.0) + bs.get('sbsn_tidak_lancar', {}).get(col, 0.0)
        # deposito_invest
        if 'deposito_invest' not in bs:
            bs['deposito_invest'] = {}
            for col in set(list(bs.get('deposito_lancar', {}).keys()) + list(bs.get('deposito_tidak_lancar', {}).keys())):
                bs['deposito_invest'][col] = bs.get('deposito_lancar', {}).get(col, 0.0) + bs.get('deposito_tidak_lancar', {}).get(col, 0.0)
        # reksadana_invest
        if 'reksadana_invest' not in bs:
            bs['reksadana_invest'] = {}
            for col in set(list(bs.get('reksadana_lancar', {}).keys()) + list(bs.get('reksadana_tidak_lancar', {}).keys())):
                bs['reksadana_invest'][col] = bs.get('reksadana_lancar', {}).get(col, 0.0) + bs.get('reksadana_tidak_lancar', {}).get(col, 0.0)
        # ijk_receivable
        if 'ijk_receivable' not in bs:
            bs['ijk_receivable'] = bs.get('piutang_ijk_lancar', {}).copy()
        # claims_receivable
        if 'claims_receivable' not in bs:
            bs['claims_receivable'] = bs.get('piutang_co_guarantee_lancar', {}).copy()
        # unearned_premium_reserve
        if 'unearned_premium_reserve' not in bs:
            bs['unearned_premium_reserve'] = bs.get('deferred_ijk_lancar', {}).copy()
        # claims_reserve_retention
        if 'claims_reserve_retention' not in bs:
            bs['claims_reserve_retention'] = bs.get('claims_reserve', {}).copy()

    return result
