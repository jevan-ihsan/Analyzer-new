import pandas as pd
import numpy as np
from utils import detect_current_period

def calculate_ratios(parsed_data):
    """
    Computes key financial and performance ratios from parsed data.
    All calculations are returned as clean values, handling division by zero.
    Missing source data is reported in ratios['data_warnings'] instead of
    being substituted with invented numbers.
    """
    pl = parsed_data.get('pl_data', {})
    bs = parsed_data.get('bs_data', {})
    gr = parsed_data.get('gr_data', {})
    mitra = parsed_data.get('mitra_data', [])

    ratios = {}
    data_warnings = []
    period = detect_current_period(pl, bs)
    ratios['period'] = period
    
    # 1. Underwriting Performance
    # Retrieve current month YTD values
    ijk_bruto_raw = pl.get('ijk_revenue', {}).get('curr_month', 0.0)
    reinsurance_expense_uw = pl.get('reinsurance_expense', {}).get('curr_month', 0.0)
    change_unearned_ijk = pl.get('change_unearned_ijk', {}).get('curr_month', 0.0)

    # Pendapatan Premi Neto (official) = IJK Bruto - Beban Reasuransi + (Penurunan/Kenaikan IJK YBMP)
    # change_unearned_ijk keeps its natural sign (negative when unearned reserve grows).
    if abs(ijk_bruto_raw) > 0 and (abs(reinsurance_expense_uw) > 0 or change_unearned_ijk != 0.0):
        net_premium_neto = abs(ijk_bruto_raw) - abs(reinsurance_expense_uw) + change_unearned_ijk
    else:
        # Fallback: use the parsed net_underwriting_revenue directly from Excel
        net_premium_neto = pl.get('net_underwriting_revenue', {}).get('curr_month', 0.0)
    # Kept under the old name for downstream consumers (KPI cards, etc.)
    net_uw_income = net_premium_neto if net_premium_neto > 0 else pl.get('net_underwriting_revenue', {}).get('curr_month', 0.0)

    gross_claims = pl.get('gross_claims', {}).get('curr_month', 0.0)
    gross_claims_prev = pl.get('gross_claims', {}).get('yoy_prev', 0.0)
    yoy_gross_claims = ((gross_claims - gross_claims_prev) / abs(gross_claims_prev) * 100.0) if gross_claims_prev != 0 else 0.0

    reinsurance_claims = pl.get('reinsurance_claims', {}).get('curr_month', 0.0)
    change_claims_retention = pl.get('change_claims_retention', {}).get('curr_month', 0.0)
    net_recoveries = pl.get('net_recoveries', {}).get('curr_month', 0.0)
    net_recoveries_prev = pl.get('net_recoveries', {}).get('yoy_prev', 0.0)
    yoy_net_recoveries = ((net_recoveries - net_recoveries_prev) / abs(net_recoveries_prev) * 100.0) if net_recoveries_prev != 0 else 0.0

    # Beban Klaim Bersih (official) = Ta'widh Bruto - Ta'widh Reas + Kenaikan ETRS - Recoveries
    # change_claims_retention keeps its natural sign (positive when reserve grows).
    claims_net = abs(gross_claims) - abs(reinsurance_claims) + change_claims_retention - abs(net_recoveries)

    loss_ratio = (claims_net / net_premium_neto * 100.0) if net_premium_neto > 0 else 0.0

    # Komisi Neto = beban akuisisi (net) - komisi penjaminan ulang (net); negative = net income.
    commission_expense_net = abs(pl.get('commission_expense', {}).get('curr_month', 0.0))
    reinsurance_commission_net = abs(pl.get('reinsurance_commission', {}).get('curr_month', 0.0))
    net_commission = commission_expense_net - reinsurance_commission_net

    investment_income_uw = pl.get('investment_income', {}).get('curr_month', 0.0)
    total_opex = abs(pl.get('total_operating_expense', {}).get('curr_month', 0.0))
    total_opex_rkap = abs(pl.get('total_operating_expense', {}).get('rkap_fy', 0.0))
    # Expense Ratio (official) = (Beban Usaha + Komisi Neto) / (Pendapatan Premi Neto + Hasil Investasi)
    expense_denom = net_premium_neto + investment_income_uw
    expense_ratio = ((total_opex + net_commission) / expense_denom * 100.0) if expense_denom > 0 else 0.0
    combined_ratio = loss_ratio + expense_ratio

    ijk_bruto = pl.get('ijk_revenue', {}).get('curr_month', 0.0)
    ijk_bruto_prev = pl.get('ijk_revenue', {}).get('yoy_prev', 0.0)
    yoy_ijk_bruto = ((ijk_bruto - ijk_bruto_prev) / abs(ijk_bruto_prev) * 100.0) if ijk_bruto_prev != 0 else 0.0
    
    reinsurance_expense = abs(pl.get('reinsurance_expense', {}).get('curr_month', 0.0))
    cession_rate = (reinsurance_expense / ijk_bruto * 100.0) if ijk_bruto > 0 else 0.0
    
    claims_receivable = bs.get('claims_receivable', {}).get('curr_month', 0.0)
    claims_receivable_prev = bs.get('claims_receivable', {}).get('prev_year_yoy', 0.0)
    yoy_claims_receivable = ((claims_receivable - claims_receivable_prev) / abs(claims_receivable_prev) * 100.0) if claims_receivable_prev != 0 else 0.0
    
    ratios['underwriting'] = {
        'loss_ratio': loss_ratio,
        'expense_ratio': expense_ratio,
        'combined_ratio': combined_ratio,
        'cession_rate': cession_rate,
        'claims_net_juta': claims_net,
        'gross_claims_juta': abs(gross_claims),
        'gross_claims_prev_juta': abs(gross_claims_prev),
        'yoy_gross_claims_pct': yoy_gross_claims,
        'net_recoveries_juta': abs(net_recoveries),
        'net_recoveries_prev_juta': abs(net_recoveries_prev),
        'yoy_net_recoveries_pct': yoy_net_recoveries,
        'claims_receivable_juta': abs(claims_receivable),
        'yoy_claims_receivable_pct': yoy_claims_receivable,
        'ijk_bruto_juta': abs(ijk_bruto),
        'yoy_ijk_bruto_pct': yoy_ijk_bruto,
        'total_opex_juta': total_opex,
        'total_opex_rkap_juta': total_opex_rkap,
        'net_uw_income_juta': net_uw_income
    }
    
    # 2. Profitability
    net_profit = pl.get('net_profit', {}).get('curr_month', 0.0)
    net_profit_yoy_prev = pl.get('net_profit', {}).get('yoy_prev', 0.0)
    yoy_net_profit_growth = ((net_profit - net_profit_yoy_prev) / abs(net_profit_yoy_prev) * 100.0) if net_profit_yoy_prev != 0 else 0.0
    
    rkap_ytd = pl.get('net_profit', {}).get('rkap_ytd', 0.0)
    rkap_fy = pl.get('net_profit', {}).get('rkap_fy', 0.0)
    
    # YoY Comparison for Laba Sebelum Pajak
    pretax_profit_curr = pl.get('pretax_profit', {}).get('curr_month', 0.0)
    pretax_profit_prev = pl.get('pretax_profit', {}).get('yoy_prev', 0.0)
    yoy_profit_growth = ((pretax_profit_curr - pretax_profit_prev) / abs(pretax_profit_prev) * 100.0) if pretax_profit_prev != 0 else 0.0
    
    # MoM Comparison for Laba Setelah Pajak
    net_profit_prev_month = pl.get('net_profit', {}).get('prev_month', 0.0)
    mom_profit_growth = ((net_profit - net_profit_prev_month) / abs(net_profit_prev_month) * 100.0) if net_profit_prev_month != 0 else 0.0
    
    invest_income_curr = pl.get('investment_income', {}).get('curr_month', 0.0)
    invest_income_prev = pl.get('investment_income', {}).get('yoy_prev', 0.0)
    yoy_invest_income_growth = ((invest_income_curr - invest_income_prev) / abs(invest_income_prev) * 100.0) if invest_income_prev != 0 else 0.0
    
    ratios['profitability'] = {
        'net_profit_juta': net_profit,
        'net_profit_prev_juta': net_profit_yoy_prev,
        'yoy_net_profit_growth_pct': yoy_net_profit_growth,
        'pretax_profit_juta': pretax_profit_curr,
        'rkap_ytd_juta': rkap_ytd,
        'rkap_fy_juta': rkap_fy,
        'rkap_ytd_achieved_pct': (net_profit / rkap_ytd * 100.0) if rkap_ytd > 0 else 0.0,
        'rkap_fy_achieved_pct': (net_profit / rkap_fy * 100.0) if rkap_fy > 0 else 0.0,
        'yoy_profit_growth_pct': yoy_profit_growth,
        'mom_profit_growth_pct': mom_profit_growth,
        'investment_income_juta': invest_income_curr,
        'yoy_invest_income_growth_pct': yoy_invest_income_growth
    }
    
    # 3. Capital & Solvency (Gearing)
    # Check if gearing sheet parsed data.
    os_net = gr.get('os_net', 0.0)
    equity = gr.get('equity', 0.0)
    gearing_ratio = gr.get('gearing_ratio', 0.0)
    
    # If gearing sheet didn't provide equity, fallback to Balance Sheet equity (real data, not hardcoded)
    if equity == 0.0:
        equity = bs.get('total_equity', {}).get('curr_month', 0.0)
        
    if gearing_ratio == 0.0 and equity > 0.0:
        gearing_ratio = os_net / equity
        
    if gearing_ratio == 0.0:
        data_warnings.append(
            "Data gearing ratio (lembar evaluasi/outstanding penjaminan) tidak ditemukan — "
            "KPI gearing menampilkan nol, bukan nilai aktual."
        )

    limit_gearing = 40.0
    headroom = limit_gearing - gearing_ratio
    additional_capacity_triliun = (headroom * equity) / 1_000_000.0 if equity > 0 else 0.0
    
    ratios['solvency'] = {
        'os_net_juta': os_net,
        'equity_juta': equity,
        'gearing_ratio': gearing_ratio,
        'limit_gearing': limit_gearing,
        'headroom': headroom,
        'additional_capacity_triliun': additional_capacity_triliun
    }
    
    # 4. Investment Portfolio structure
    sbsn = bs.get('sbsn_invest', {}).get('curr_month', 0.0)
    deposito = bs.get('deposito_invest', {}).get('curr_month', 0.0)
    reksadana = bs.get('reksadana_invest', {}).get('curr_month', 0.0)
    total_invest = sbsn + deposito + reksadana
    
    ratios['investment'] = {
        'sbsn_juta': sbsn,
        'deposito_juta': deposito,
        'reksadana_juta': reksadana,
        'total_invest_juta': total_invest,
        'sbsn_share': (sbsn / total_invest * 100.0) if total_invest > 0 else 0.0,
        'deposito_share': (deposito / total_invest * 100.0) if total_invest > 0 else 0.0,
        'reksadana_share': (reksadana / total_invest * 100.0) if total_invest > 0 else 0.0
    }
    
    # 5. Partner Concentration
    total_os_kafalah = sum(m['os_kafalah_juta'] for m in mitra)
    concentration_list = []
    
    # Compute market shares and HHI (Herfindahl-Hirschman Index)
    hhi = 0.0
    bsi_share = 0.0
    pnm_share = 0.0
    top3_share = 0.0
    
    if total_os_kafalah > 0:
        # Sort bank partners by outstanding kafalah descending to properly identify top 3
        sorted_mitra = sorted(mitra, key=lambda x: x['os_kafalah_juta'], reverse=True)
        for idx, m in enumerate(sorted_mitra):
            share = (m['os_kafalah_juta'] / total_os_kafalah) * 100.0
            hhi += (share ** 2)
            
            if 'bsi' in m['partner'].lower():
                bsi_share = share
            elif 'pnm' in m['partner'].lower():
                pnm_share = share
                
            if idx < 3:
                top3_share += share
                
            concentration_list.append({
                'partner': m['partner'],
                'os_kafalah_juta': m['os_kafalah_juta'],
                'share_pct': share
            })
    else:
        # No partner data parsed — report it honestly instead of substituting stale numbers.
        data_warnings.append(
            "Data mitra (plafon/rekapan kafalah) tidak ditemukan pada file yang diunggah — "
            "analisis konsentrasi mitra tidak tersedia."
        )

    # 6. DuPont 5-Factor Analysis
    ebt = pl.get('pretax_profit', {}).get('curr_month', 0.0)
    ebit = pl.get('operating_profit', {}).get('curr_month', 0.0)
    revenue = abs(ijk_bruto)
    total_assets = bs.get('total_assets', {}).get('curr_month', 0.0)

    # BS and PL data are in full Rupiah; gr_data equity is in Jutaan — use BS equity for DuPont
    equity_bs = bs.get('total_equity', {}).get('curr_month', 0.0)
    if equity_bs == 0.0:
        equity_bs = equity * 1_000_000.0  # fallback: convert gr_data Jutaan → full Rp

    if total_assets == 0.0:
        data_warnings.append(
            "Total Aset tidak ditemukan di Neraca — faktor DuPont (asset turnover, leverage, ROE) tidak dapat dihitung."
        )
    if ebit == 0.0 and ebt != 0.0:
        # Laba Usaha row missing; approximate EBIT with EBT (assumes no non-operating items).
        ebit = ebt
        data_warnings.append(
            "Laba Usaha (EBIT) tidak ditemukan — interest burden DuPont diasumsikan 1,0 (EBIT = EBT)."
        )

    tax_burden = net_profit / ebt if ebt != 0 else 0.0
    interest_burden = ebt / ebit if ebit != 0 else 0.0
    ebit_margin = ebit / revenue if revenue != 0 else 0.0
    asset_turnover = revenue / total_assets if total_assets != 0 else 0.0
    leverage = total_assets / equity_bs if equity_bs != 0 else 1.0
    
    roe = tax_burden * interest_burden * ebit_margin * asset_turnover * leverage * 100.0
    
    ratios['dupont'] = {
        'tax_burden': tax_burden,
        'interest_burden': interest_burden,
        'ebit_margin': ebit_margin,
        'asset_turnover': asset_turnover,
        'leverage': leverage,
        'roe_pct': roe
    }
    
    # 7. Skor Kesehatan OJK (Approximation)
    # 1. Likuiditas: approx current assets / current liab
    # 2. Gearing
    # 3. ROA: (Net Profit disetahunkan / Rata-rata Aset) * 100
    # Annualize YTD net profit using the number of elapsed months in the detected period
    month_factor = float(period['month'])
    net_profit_annualized = net_profit * 12.0 / month_factor
    
    total_assets_prev = bs.get('total_assets', {}).get('prev_year_yoy', 0.0)
    avg_assets = (total_assets + total_assets_prev) / 2.0 if total_assets_prev > 0.0 else total_assets
    roa = (net_profit_annualized / avg_assets) * 100.0 if avg_assets > 0.0 else 0.0
    
    # BOPO (official) = (Beban Klaim Neto + Beban Usaha) / (Pendapatan Underwriting Neto + Hasil Investasi)
    bopo_denominator = net_uw_income + invest_income_curr
    bopo = ((claims_net + total_opex) / bopo_denominator * 100.0) if bopo_denominator > 0 else 0.0
    # 5. Klaim Neto: (Net Claims / Net UW Income) = loss_ratio
    # Nilai mapping (simplified)
    score_gearing = 1 if gearing_ratio <= 30 else (2 if gearing_ratio <= 40 else 3)
    score_roa = 1 if roa >= 5 else (2 if roa >= 3 else 3)
    score_bopo = 1 if bopo <= 60 else (2 if bopo <= 80 else 3)
    score_klaim = 1 if loss_ratio <= 60 else (2 if loss_ratio <= 80 else 3)
    score_gcg = 2 # Assuming constant
    
    # Weighted average (Approximation: 10% likuiditas(1), 35% gearing, 10.5% roa, 12.25% bopo, 12.25% klaim, 20% GCG)
    composite_score = (1 * 0.1) + (score_gearing * 0.35) + (score_roa * 0.105) + (score_bopo * 0.1225) + (score_klaim * 0.1225) + (score_gcg * 0.20)
    
    ratios['ojk_health'] = {
        'roa_pct': roa,
        'bopo_pct': bopo,
        'score_gearing': score_gearing,
        'score_roa': score_roa,
        'score_bopo': score_bopo,
        'score_klaim': score_klaim,
        'composite_score': composite_score
    }
    
    # 8. OJK PADK 47 Ratios for Sharia Guarantee Company (JPAS)
    total_curr_assets = bs.get('total_current_assets', {}).get('curr_month', 0.0)
    total_curr_liabilities = bs.get('total_current_liabilities', {}).get('curr_month', 0.0)
    total_liabilities_val = bs.get('total_liabilities', {}).get('curr_month', 0.0)
    
    cash_giro = bs.get('cash_and_bank', {}).get('curr_month', 0.0)
    deposito_l = bs.get('deposito_lancar', {}).get('curr_month', 0.0)
    sbsn_l = bs.get('sbsn_lancar', {}).get('curr_month', 0.0)
    reksadana_l = bs.get('reksadana_lancar', {}).get('curr_month', 0.0)
    aset_likuid = cash_giro + deposito_l + sbsn_l + reksadana_l
    
    claims_reserve_val = bs.get('claims_reserve', {}).get('curr_month', 0.0)
    
    utang_klaim = bs.get('claims_payable_lancar', {}).get('curr_month', 0.0)
    utang_komisi = bs.get('commission_payable', {}).get('curr_month', 0.0)
    utang_klaim_coguar = bs.get('co_guarantee_claims_payable', {}).get('curr_month', 0.0)
    utang_ijk_coguar = bs.get('co_guarantee_ijk_payable', {}).get('curr_month', 0.0)
    utang_reas = bs.get('reinsurance_payable', {}).get('curr_month', 0.0)
    utang_penjaminan = utang_klaim + utang_komisi + utang_klaim_coguar + utang_ijk_coguar + utang_reas
    
    # 1. Komposisi Aset Lancar
    c_komp = (total_curr_assets / total_assets * 100.0) if total_assets > 0 else 0.0
    # 2. Current Ratio
    c_curr = (total_curr_assets / total_curr_liabilities * 100.0) if total_curr_liabilities > 0 else 0.0
    # 3. Kecukupan Aset Likuid terhadap Klaim Dilaporkan
    c_lik_claim = (aset_likuid / claims_reserve_val * 100.0) if claims_reserve_val > 0 else 0.0
    # 4. Rasio Kecukupan Kas dan Giro terhadap Utang Penjaminan
    c_kas_utang = (cash_giro / utang_penjaminan * 100.0) if utang_penjaminan > 0 else 0.0
    
    # 5. Kecukupan Investasi terhadap Cadangan Klaim
    deposito_tl = bs.get('deposito_tidak_lancar', {}).get('curr_month', 0.0)
    sbsn_tl = bs.get('sbsn_tidak_lancar', {}).get('curr_month', 0.0)
    reksadana_tl = bs.get('reksadana_tidak_lancar', {}).get('curr_month', 0.0)
    saham_l = bs.get('saham_lancar', {}).get('curr_month', 0.0)
    saham_tl = bs.get('saham_tidak_lancar', {}).get('curr_month', 0.0)
    sukuk_l = bs.get('sukuk_korporasi_lancar', {}).get('curr_month', 0.0)
    sukuk_tl = bs.get('sukuk_korporasi_tidak_lancar', {}).get('curr_month', 0.0)
    mtn_l = bs.get('mtn_lancar', {}).get('curr_month', 0.0)
    mtn_tl = bs.get('mtn_tidak_lancar', {}).get('curr_month', 0.0)
    
    total_investasi = deposito_l + deposito_tl + sbsn_l + sbsn_tl + reksadana_l + reksadana_tl + saham_l + saham_tl + sukuk_l + sukuk_tl + mtn_l + mtn_tl
    c_inv_claim = (total_investasi / claims_reserve_val * 100.0) if claims_reserve_val > 0 else 0.0
    
    # 6. Kecukupan Aset Lancar terhadap Beban Klaim (Net Claims)
    ijk_ckpn = abs(bs.get('piutang_ijk_ckpn_lancar', {}).get('curr_month', 0.0))
    c_assets_claim = ((total_curr_assets - ijk_ckpn) / claims_net * 100.0) if claims_net > 0 else 0.0
    
    # 7. Kecukupan Aset Likuid terhadap Klaim Disetujui
    c_lik_disetujui = (aset_likuid / utang_klaim * 100.0) if utang_klaim > 0 else 0.0
    
    # 8. Kecukupan Aset Likuid terhadap Proyeksi Klaim Jangka Pendek
    c_lik_proyeksi = (aset_likuid / (claims_reserve_val * 1.2) * 100.0) if claims_reserve_val > 0 else 0.0
    
    # Rentabilitas
    total_equity_curr = bs.get('total_equity', {}).get('curr_month', 0.0)
    total_equity_prev = bs.get('total_equity', {}).get('prev_year_yoy', 0.0)
    avg_equity = (total_equity_curr + total_equity_prev) / 2.0 if total_equity_prev > 0.0 else total_equity_curr
    
    roa_syariah = roa
    roe_syariah = (net_profit_annualized / avg_equity * 100.0) if avg_equity > 0.0 else 0.0
    
    # BOPO Syariah
    bopo_syariah = ((claims_net + total_opex) / (net_uw_income + invest_income_curr) * 100.0) if (net_uw_income + invest_income_curr) > 0 else 0.0
    
    # Leverage Ratio OJK
    leverage_ojk = (total_liabilities_val / total_equity_curr) if total_equity_curr > 0 else 0.0
    
    ratios['ojk_padk47'] = {
        'komposisi_aset_lancar': c_komp,
        'current_ratio': c_curr,
        'aset_likuid_vs_klaim_dilaporkan': c_lik_claim,
        'kas_giro_vs_utang_penjaminan': c_kas_utang,
        'investasi_vs_cadangan_klaim': c_inv_claim,
        'aset_lancar_vs_beban_klaim': c_assets_claim,
        'aset_likuid_vs_klaim_disetujui': c_lik_disetujui,
        'aset_likuid_vs_proyeksi_klaim': c_lik_proyeksi,
        'roa_syariah': roa_syariah,
        'roe_syariah': roe_syariah,
        'bopo_syariah': bopo_syariah,
        'net_claim_ratio_syariah': loss_ratio,
        'pertumbuhan_ijk_syariah': yoy_ijk_bruto,
        'leverage_ratio_ojk': leverage_ojk
    }
    
    ratios['concentration'] = {
        'total_os_kafalah_juta': total_os_kafalah,
        'bsi_share_pct': bsi_share,
        'pnm_share_pct': pnm_share,
        'top3_share_pct': top3_share,
        'hhi_index': hhi,
        'mitra_shares': concentration_list
    }

    ratios['data_warnings'] = data_warnings
    return ratios
