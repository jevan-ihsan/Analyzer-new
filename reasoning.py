import pandas as pd
from utils import format_id

def generate_takeaways_and_critique(ratios, parsed=None):
    """
    Generates strategic takeaways, warnings, and critical thinking explanations
    in Indonesian (Bahasa Indonesia) based on the computed ratios and findings.
    """
    takeaways = {
        'positif': [],
        'negatif': [],
        'kritis': [],
        'tindak_lanjut': [],
        'anomali_dinamis': []
    }
    
    # ------------------ ANOMALI DINAMIS ENGINE ------------------
    if parsed:
        pl_data = parsed.get('pl_data', {})
        bs_data = parsed.get('bs_data', {})
        
        def format_rupiah(val):
            abs_val = abs(val)
            sign = "-" if val < 0 else ""
            if abs_val >= 1_000_000_000:
                return f"Rp {sign}{format_id(abs_val / 1_000_000_000.0, decimals=2)} M"
            elif abs_val >= 1_000_000:
                return f"Rp {sign}{format_id(abs_val / 1_000_000.0, decimals=2)} Juta"
            else:
                return f"Rp {sign}{format_id(abs_val, decimals=0)}"
            
        def scan_anomalies(data_dict, category_name):
            for item_name, item_data in data_dict.items():
                if not isinstance(item_data, dict): continue
                
                val1 = item_data.get('prev_month') or item_data.get('yoy_prev') or 0
                val2 = item_data.get('curr_month') or 0
                
                try:
                    val1 = float(val1)
                    val2 = float(val2)
                except (TypeError, ValueError):
                    continue
                    
                if val1 != 0:
                    diff = val2 - val1
                    pct_change = diff / abs(val1)
                    
                    # Data dalam satuan Rupiah
                    # Threshold: > 25% perubahan DAN > Rp 1 Miliar
                    if abs(pct_change) >= 0.25 and abs(diff) >= 1_000_000_000:
                        arah = "Lonjakan" if pct_change > 0 else "Penurunan"
                        nama_lower = str(item_name).lower()
                        if pct_change > 0 and ("laba" in nama_lower or "pendapatan" in nama_lower or "profit" in nama_lower):
                            tipe = "positif"
                        elif pct_change < 0 and ("laba" in nama_lower or "pendapatan" in nama_lower or "profit" in nama_lower):
                            tipe = "negatif"
                        elif pct_change < 0 and ("klaim" in nama_lower or "beban" in nama_lower or "claim" in nama_lower):
                            tipe = "positif"
                        elif pct_change > 0 and ("klaim" in nama_lower or "beban" in nama_lower or "claim" in nama_lower):
                            tipe = "negatif"
                        else:
                            tipe = "peringatan"
                        
                        takeaways['anomali_dinamis'].append({
                            'tipe': tipe,
                            'title': f"{arah} Signifikan pada '{item_name}' ({category_name})",
                            'content': f"Berubah sebesar **{format_id(pct_change*100, decimals=1, prefix='+') if pct_change > 0 else format_id(pct_change*100, decimals=1)}%** dari {format_rupiah(val1)} menjadi {format_rupiah(val2)}. "
                                       f"Selisih nominal mencapai **{format_rupiah(diff)}**. "
                                       f"Sistem merekomendasikan investigasi langsung terhadap pos ini karena menyimpang drastis dari periode sebelumnya."
                        })

        scan_anomalies(pl_data, "Laba Rugi")
        scan_anomalies(bs_data, "Neraca")
    # ------------------------------------------------------------

    uw = ratios.get('underwriting', {})
    prof = ratios.get('profitability', {})
    solv = ratios.get('solvency', {})
    inv = ratios.get('investment', {})
    conc = ratios.get('concentration', {})
    
    # 1. Temuan Kritis (Tindakan Segera)
    kritis = []
    
    # K1: Tekanan Klaim Ta'widh
    yoy_gross_claims = format_id(uw.get('yoy_gross_claims_pct', 0), decimals=1)
    gross_claims_prev = format_id(uw.get('gross_claims_prev_juta', 0) / 1_000_000_000.0, decimals=1)
    gross_claims_curr = format_id(uw.get('gross_claims_juta', 0) / 1_000_000_000.0, decimals=1)
    yoy_ijk_bruto = format_id(uw.get('yoy_ijk_bruto_pct', 0), decimals=1)
    
    kritis.append(
        f"**K1: Tekanan Klaim Ta'widh**: Beban klaim (Ta'widh bruto) tumbuh sebesar {yoy_gross_claims}% YoY "
        f"(dari Rp {gross_claims_prev} M ke Rp {gross_claims_curr} M), "
        f"dibandingkan dengan laju pertumbuhan pendapatan (IJK Bruto) sebesar {yoy_ijk_bruto}%."
    )
    
    # K2: Kemampuan Penagihan
    if uw.get('yoy_net_recoveries_pct', 0) < 0:
        yoy_net_rec = format_id(abs(uw.get('yoy_net_recoveries_pct', 0)), decimals=1)
        net_rec_prev = format_id(uw.get('net_recoveries_prev_juta', 0) / 1_000_000_000.0, decimals=1)
        net_rec_curr = format_id(uw.get('net_recoveries_juta', 0) / 1_000_000_000.0, decimals=1)
        
        kritis.append(
            f"**K2: Kemampuan Pemulihan (Recoveries) Anjlok**: Penerimaan pemulihan hak tagih (subrogasi kas) turun sebesar {yoy_net_rec}% YoY "
            f"(dari Rp {net_rec_prev} M ke Rp {net_rec_curr} M), "
            "mengurangi faktor pengurang beban klaim neto."
        )
    
    # K3: Akumulasi Piutang Ta'widh
    if uw.get('yoy_claims_receivable_pct', 0) > 0:
        yoy_claims_rec = format_id(uw.get('yoy_claims_receivable_pct', 0), decimals=1)
        kritis.append(
            f"**K3: Akumulasi Piutang Ta'widh**: Saldo Piutang Ta'widh (klaim dibayar namun belum tertagih dari debitur) meningkat {yoy_claims_rec}% YoY. "
            "Hal ini mengindikasikan perlunya perhatian pada penagihan piutang."
        )
        
    # K4: Risiko Konsentrasi
    if conc.get('top3_share_pct', 0) > 50:
        top3_share = format_id(conc.get('top3_share_pct', 0), decimals=1)
        bsi_share = format_id(conc.get('bsi_share_pct', 0), decimals=1)
        kritis.append(
            f"**K4: Risiko Konsentrasi Tinggi**: Sebesar {top3_share}% dari total portofolio Kafalah "
            f"terkonsentrasi pada 3 mitra utama, dengan mitra terbesar menguasai {bsi_share}%."
        )

    # 2. Temuan Perhatian (Monitor Aktif)
    perhatian = []
    
    # P1: Laba Bersih
    if prof.get('yoy_net_profit_growth_pct', 0) < 0:
        yoy_net_profit = format_id(abs(prof.get('yoy_net_profit_growth_pct', 0)), decimals=1)
        net_profit_prev = format_id(prof.get('net_profit_prev_juta', 0) / 1_000_000_000.0, decimals=1)
        net_profit_curr = format_id(prof.get('net_profit_juta', 0) / 1_000_000_000.0, decimals=1)
        
        perhatian.append(
            f"**P1: Penurunan Laba Bersih**: Laba bersih turun sebesar {yoy_net_profit}% YoY "
            f"(dari Rp {net_profit_prev} M ke Rp {net_profit_curr} M) meskipun pendapatan (IJK) mengalami pertumbuhan."
        )
    
    # P2: Sisa Kapasitas (Gearing Headroom)
    gearing_ratio = solv.get('gearing_ratio', 0)
    limit = solv.get('limit_gearing', 40)
    headroom_pct = (gearing_ratio / limit) * 100 if limit > 0 else 0
    if headroom_pct > 70:
        gearing_val = format_id(gearing_ratio, decimals=2)
        headroom_val = format_id(headroom_pct, decimals=1)
        limit_val = format_id(limit, decimals=1)
        add_capacity = format_id(solv.get('additional_capacity_triliun', 0), decimals=2)
        
        perhatian.append(
            f"**P2: Kapasitas Gearing Mendekati Batas**: Gearing ratio aktual mencapai {gearing_val}x, "
            f"mewakili {headroom_val}% dari batas maksimum POJK sebesar {limit_val}x. Sisa ruang ekspansi penjaminan neto "
            f"tercatat sebesar Rp {add_capacity} Triliun."
        )
        
    # P3: Serapan Beban Usaha — only when the file actually contains an RKAP budget
    if uw.get('total_opex_rkap_juta', 0) > 0:
        opex_pct = (uw.get('total_opex_juta', 0) / uw['total_opex_rkap_juta']) * 100
        opex_pct_val = format_id(opex_pct, decimals=1)
        total_opex = format_id(uw.get('total_opex_juta', 0) / 1_000_000_000.0, decimals=1)
        total_opex_rkap = format_id(uw['total_opex_rkap_juta'] / 1_000_000_000.0, decimals=1)

        perhatian.append(
            f"**P3: Serapan Anggaran Beban Usaha**: Serapan anggaran Beban Usaha mencapai {opex_pct_val}% YTD "
            f"(Rp {total_opex} M dari plafon RKAP Rp {total_opex_rkap} M)."
        )

    # 3. Temuan Positif (Kekuatan & Integritas)
    positif = []
    
    if uw.get('expense_ratio', 100) < 60:
        exp_ratio = format_id(uw.get('expense_ratio', 0), decimals=1)
        positif.append(
            f"**H1: Efisiensi Beban Operasional**: Rasio beban (expense ratio) tercatat sangat efisien di level {exp_ratio}%."
        )
        
    # Investasi
    invest_share_sbsn = inv.get('sbsn_share', 0)
    if invest_share_sbsn > 50:
        sbsn_share = format_id(invest_share_sbsn, decimals=1)
        positif.append(
            f"**H2: Portofolio Investasi Aman**: Mayoritas portofolio investasi dialokasikan pada aset berisiko rendah "
            f"yaitu Surat Berharga Syariah Negara (SBSN) sebesar {sbsn_share}%."
        )
        
    if prof.get('rkap_ytd_achieved_pct', 0) > 90:
        rkap_ytd_achieved = format_id(prof.get('rkap_ytd_achieved_pct', 0), decimals=1)
        positif.append(
            f"**H3: Pencapaian Laba Kuat**: Realisasi laba telah mencapai {rkap_ytd_achieved}% dari target anggaran YTD."
        )

    # 4. Critical Thinking & Financial Ratio Analysis (Sebab-Akibat)
    critical_analysis = {}
    
    # Kinerja Underwriting
    loss_ratio_desc = "rendah" if uw.get('loss_ratio', 0) < 40 else "moderat" if uw.get('loss_ratio', 0) < 70 else "tinggi"
    loss_ratio = format_id(uw.get('loss_ratio', 0), decimals=2)
    gross_claims = format_id(uw.get('gross_claims_juta', 0) / 1_000_000_000.0, decimals=1)
    opex_m = format_id(uw.get('total_opex_juta', 0) / 1_000_000_000.0, decimals=1)
    combined_ratio = format_id(uw.get('combined_ratio', 0), decimals=2)
    net_uw_income = format_id(uw.get('net_uw_income_juta', 0) / 1_000_000_000.0, decimals=1)
    
    critical_analysis['kinerja_underwriting'] = (
        "**Analisis Kinerja Underwriting (Rasio Kerugian & Gabungan)**:\n"
        f"1. **Tingkat Klaim**: Rasio Kerugian Neto (Net Loss Ratio) tercatat di level {loss_ratio}%. Tingkat ini masuk kategori {loss_ratio_desc}. "
        f"Beban klaim bruto mencapai Rp {gross_claims} M.\n"
        f"2. **Rasio Gabungan (Combined Ratio)**: Ditopang oleh Beban Usaha sebesar Rp {opex_m} M, "
        f"Rasio Gabungan berada di tingkat {combined_ratio}%. Laba teknik underwriting yang dihasilkan "
        f"sebesar Rp {net_uw_income} M."
    )
    
    # Kinerja Profitabilitas
    profit_trend = "mengalami pertumbuhan" if prof.get('yoy_net_profit_growth_pct', 0) >= 0 else "mengalami kontraksi"
    net_profit = format_id(prof.get('net_profit_juta', 0) / 1_000_000_000.0, decimals=1)
    yoy_profit_growth = format_id(prof.get('yoy_net_profit_growth_pct', 0), decimals=1)
    rkap_fy_achieved = format_id(prof.get('rkap_fy_achieved_pct', 0), decimals=2)
    invest_income = format_id(prof.get('investment_income_juta', 0) / 1_000_000_000.0, decimals=1)
    yoy_invest_growth = format_id(prof.get('yoy_invest_income_growth_pct', 0), decimals=1)
    
    critical_analysis['kinerja_profitabilitas'] = (
        "**Analisis Profitabilitas dan Kinerja RKAP**:\n"
        f"1. **Tren Laba Bersih**: Realisasi Laba Bersih sebesar Rp {net_profit} M "
        f"{profit_trend} sebesar {yoy_profit_growth}% YoY.\n"
        f"2. **Pencapaian Target RKAP**: Kinerja profitabilitas mencapai {rkap_fy_achieved}% dari target Full Year. "
        f"Hasil investasi menyumbang Rp {invest_income} M dengan pertumbuhan YoY sebesar {yoy_invest_growth}%."
    )
    
    # Risiko Konsentrasi
    hhi = format_id(conc.get('hhi_index', 0), decimals=0)
    top3_share = format_id(conc.get('top3_share_pct', 0), decimals=2)
    
    critical_analysis['risiko_konsentrasi'] = (
        "**Analisis Risiko Konsentrasi Portofolio Mitra**:\n"
        f"1. **Indeks Konsentrasi**: Konsentrasi portofolio dengan HHI Index mencapai {hhi}. "
        f"Sebesar {top3_share}% dari total outstanding dikuasai oleh 3 mitra teratas.\n"
        "2. **Kerentanan Sistemik**: Tingkat konsentrasi ini menuntut perusahaan untuk secara aktif "
        "memperluas dan mendiversifikasi mitra penjaminan untuk memitigasi risiko default sektoral atau perubahan kebijakan mitra utama."
    )
    
    # Solvabilitas & Regulasi
    gearing_ratio_val = format_id(solv.get('gearing_ratio', 0), decimals=2)
    headroom_val = format_id(solv.get('headroom', 0), decimals=2)
    add_capacity_tril = format_id(solv.get('additional_capacity_triliun', 0), decimals=2)
    
    critical_analysis['solvabilitas_regulasi'] = (
        "**Analisis Solvabilitas dan Batas Regulasi**:\n"
        f"1. **Pemanfaatan Gearing Aktual**: Gearing ratio aktual sebesar {gearing_ratio_val}x.\n"
        f"2. **Sisa Gearing Headroom**: Sisa ruang ekspansi adalah sebesar {headroom_val}x, setara dengan tambahan kapasitas penerbitan penjaminan "
        f"sebesar Rp {add_capacity_tril} Triliun."
    )
    
    # 5. Ekuitas
    equity = format_id(solv.get('equity_juta', 0) / 1_000_000_000.0, decimals=1)
    os_net = format_id(solv.get('os_net_juta', 0) / 1_000_000_000.0, decimals=1)
    
    dupont_analysis = (
        "**Analisis Struktur Ekuitas**:\n"
        f"Perusahaan mencatatkan ekuitas sebesar Rp {equity} M untuk menopang total outstanding "
        f"Kafalah bersih sebesar Rp {os_net} M."
    )
    
    # 6. Cross-Metric Intelligence (Insight Sintesis)
    yoy_profit = prof.get('yoy_net_profit_growth_pct', 0)
    yoy_claims = uw.get('yoy_gross_claims_pct', 0)
    yoy_rev = uw.get('yoy_ijk_bruto_pct', 0)
    
    sintesis = ""
    if yoy_profit > 0 and yoy_claims < 0:
        sintesis = "**Sintesis Kualitas Pertumbuhan**: Pertumbuhan Laba Bersih yang positif diiringi dengan penurunan beban klaim menunjukkan **Pertumbuhan Berkualitas Tinggi**. Perusahaan berhasil melakukan ekspansi pendapatan tanpa mengorbankan kualitas portofolio."
    elif yoy_profit > 0 and yoy_claims > yoy_rev:
        sintesis = "**Sintesis Risiko Ekspansi**: Walaupun laba tumbuh, laju kenaikan klaim lebih cepat daripada laju kenaikan pendapatan. Ini merupakan indikasi **Pertumbuhan Berisiko (Risk-fueled Growth)**. Ekspansi volume mungkin dilakukan dengan melonggarkan standar underwriting."
    elif yoy_profit < 0 and yoy_claims > 0:
        sintesis = "**Sintesis Tekanan Ganda**: Perusahaan mengalami **Tekanan Ganda (Double Squeeze)** di mana pendapatan gagal mengkompensasi lonjakan beban klaim, yang berujung pada tergerusnya laba bersih secara signifikan."
    else:
        sintesis = "**Sintesis Stabilitas**: Dinamika antara pendapatan, klaim, dan laba berada pada rentang yang wajar, menunjukkan stabilitas operasional meskipun tanpa lonjakan kinerja yang eksponensial."
        
    critical_analysis['sintesis_intelijen'] = sintesis
    
    return {
        'kritis': kritis,
        'perhatian': perhatian,
        'positif': positif,
        'takeaways': kritis + perhatian + positif,
        'critical_analysis': critical_analysis,
        'dupont_analysis': dupont_analysis,
        'anomali_dinamis': takeaways.get('anomali_dinamis', [])
    }
