import os
import sys
import pytest
import pandas as pd
import numpy as np

# Add parent directory to path so we can import local modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from parser import (
    normalize_string,
    clean_value,
    make_columns_unique,
    format_date_header,
    _generate_combined_headers,
    _extract_header_and_data
)
from utils import format_id, classify_sheet_by_content
from analyzer import calculate_ratios

def test_normalize_string():
    assert normalize_string("  HELLO   WORLD  \n") == "hello world"
    assert normalize_string("Test String") == "test string"
    assert normalize_string("") == ""

def test_clean_value():
    assert clean_value(123) == 123.0
    assert clean_value(12.34) == 12.34
    assert clean_value("12.345.678") == 12345678.0
    assert clean_value("12,34") == 12.34
    assert clean_value("abc") == 0.0
    assert clean_value(np.nan) == 0.0

def test_make_columns_unique():
    cols = ["A", "B", "A", "C", "B"]
    unique_cols = make_columns_unique(cols)
    assert unique_cols == ["A", "B", "A_1", "C", "B_1"]

def test_format_date_header():
    # Test pandas timestamp or datetime object
    ts = pd.Timestamp("2026-05-15")
    assert format_date_header(ts) == "Mei 2026"
    
    # Test date string format YYYY-MM-DD
    assert format_date_header("2026-06-18") == "Juni 2026"
    assert format_date_header("2025-12-01") == "Desember 2025"
    
    # Test other strings
    assert format_date_header("Mei 2026") == "Mei 2026"
    assert format_date_header("not a date") == "not a date"

def test_generate_combined_headers():
    row_top = ["Aset", "Aset", "Kewajiban", "Kewajiban"]
    row_sub = ["Kas", "Bank", "Utang", "Pajak"]
    headers = _generate_combined_headers(row_top, row_sub)
    assert headers == ["Aset - Kas", "Aset - Bank", "Kewajiban - Utang", "Kewajiban - Pajak"]

def test_format_id():
    assert format_id(1234567.89) == "1.234.567,89"
    assert format_id(12.34, is_pct=True) == "12,34%"
    assert format_id(1234.56, is_currency=True) == "Rp1.234,56"
    assert format_id(1.5, is_ratio=True, decimals=1) == "1,5x"
    assert format_id(None) == "-"
    assert format_id(0.0) == "-"

def test_classify_sheet_by_content():
    # Mock a Balance Sheet DataFrame
    df_bs = pd.DataFrame([
        ["Keterangan", "Mei 2026", "April 2026"],
        ["Kas dan Bank", 100, 90],
        ["Aset Tetap", 500, 480],
        ["Total Aset", 600, 570]
    ])
    stypes = classify_sheet_by_content(df_bs)
    assert 'BS' in stypes

    # Mock a PL DataFrame
    df_pl = pd.DataFrame([
        ["Keterangan", "Mei 2026", "April 2026"],
        ["Imbal Jasa Kafalah Bruto", 1000, 900],
        ["Beban Penjaminan Ulang", 200, 180],
        ["Laba Sebelum Pajak", 100, 80]
    ])
    stypes = classify_sheet_by_content(df_pl)
    assert 'PL' in stypes

def test_calculate_ratios():
    # Test ratio computations with mock parsed state
    parsed = {
        'pl_data': {
            'net_profit': {'curr_month': 50_000_000.0, 'yoy_prev': 40_000_000.0, 'rkap_fy': 100_000_000.0},
            'pretax_profit': {'curr_month': 50_000_000.0, 'yoy_prev': 40_000_000.0},
            'ijk_revenue': {'curr_month': 200_000_000.0, 'yoy_prev': 180_000_000.0, 'rkap_fy': 300_000_000.0},
            'net_underwriting_revenue': {'curr_month': 200_000_000.0},
            'gross_claims': {'curr_month': 50_000_000.0, 'yoy_prev': 60_000_000.0},
            'total_operating_expense': {'curr_month': 30_000_000.0, 'rkap_fy': 50_000_000.0},
            'net_underwriting_result': {'curr_month': 70_000_000.0},
            'investment_income': {'curr_month': 15_000_000.0, 'yoy_prev': 12_000_000.0}
        },
        'bs_data': {
            'total_assets': {'curr_month': 1_000_000_000.0, 'prev_year_yoy': 900_000_000.0},
            'total_equity': {'curr_month': 500_000_000.0, 'prev_year_yoy': 450_000_000.0},
            'cash_and_bank': {'curr_month': 100_000_000.0},
            'sbsn_invest': {'curr_month': 200_000_000.0},
            'deposito_invest': {'curr_month': 150_000_000.0},
            'reksadana_invest': {'curr_month': 50_000_000.0},
            'unearned_premium_reserve': {'curr_month': 150_000_000.0},
            'claims_reserve_retention': {'curr_month': 50_000_000.0}
        },
        'gr_data': {
            'os_net': 150_000_000.0,
            'equity': 500_000_000.0
        }
    }
    
    ratios = calculate_ratios(parsed)
    assert ratios['solvency']['gearing_ratio'] == 0.3
    assert ratios['underwriting']['loss_ratio'] == 25.0  # 50M / 200M * 100
    assert ratios['profitability']['yoy_profit_growth_pct'] == 25.0  # (50M - 40M) / 40M * 100

# ---------------------------------------------------------------------------
# Fuzzy account matching
# ---------------------------------------------------------------------------
from parser import _fuzzy_match_account, PL_FALLBACK_SYNONYMS, BS_FALLBACK_SYNONYMS

def test_fuzzy_match_pl_variants():
    assert _fuzzy_match_account("pendapatan premi bruto", PL_FALLBACK_SYNONYMS) == "ijk_revenue"
    assert _fuzzy_match_account("laba bersih", PL_FALLBACK_SYNONYMS) == "net_profit"
    assert _fuzzy_match_account("beban klaim bruto tahun berjalan", PL_FALLBACK_SYNONYMS) == "gross_claims"

def test_fuzzy_match_respects_exclusions():
    # 'laba bersih komprehensif' must NOT map to net_profit
    assert _fuzzy_match_account("laba bersih komprehensif", PL_FALLBACK_SYNONYMS) is None
    # 'jumlah kewajiban dan ekuitas' must NOT map to total_liabilities
    assert _fuzzy_match_account("jumlah kewajiban dan ekuitas", BS_FALLBACK_SYNONYMS) is None

def test_fuzzy_match_bs_variants():
    assert _fuzzy_match_account("total aktiva", BS_FALLBACK_SYNONYMS) == "total_assets"
    assert _fuzzy_match_account("jumlah modal sendiri", BS_FALLBACK_SYNONYMS) == "total_equity"
    assert _fuzzy_match_account("keterangan acak tanpa makna", BS_FALLBACK_SYNONYMS) is None

def test_classify_sheet_substring_matching():
    # Cells contain keywords with extra words — substring matching must still classify
    df = pd.DataFrame({
        0: ["I. Kas dan Setara Kas per periode", "II. Jumlah Aktiva Lancar",
            "Total Aktiva perusahaan", "Jumlah Kewajiban jangka pendek", "Aktiva Tetap bersih"],
        1: [1, 2, 3, 4, 5],
    })
    assert "BS" in classify_sheet_by_content(df)
