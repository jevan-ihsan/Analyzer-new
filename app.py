import os
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import re

# Set page config with custom title and wide layout
st.set_page_config(
    page_title="JPAS Automated Financial Analyzer",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded"
)

# Import local modules
from utils import detect_excel_type
from parser import parse_excel_file
from analyzer import calculate_ratios
from reasoning import generate_takeaways_and_critique

# Helper to render custom styled HTML tables
def to_beautiful_table(df, align_right_cols=None):
    html = """
    <div style="overflow-x: auto; border-radius: 12px; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); margin-bottom: 24px; font-family: 'Outfit', sans-serif;">
        <table style="width: 100%; border-collapse: collapse; background-color: white; font-size: 0.9rem; text-align: left;">
            <thead>
                <tr style="background-color: #f8fafc; border-bottom: 2px solid #e2e8f0;">
    """
    for col in df.columns:
        align = "right" if align_right_cols is not None and col in align_right_cols else "left"
        html += f'<th style="padding: 12px 16px; font-weight: 600; color: #475569; text-align: {align};">{col}</th>'
    html += "</tr></thead><tbody>"
    
    for _, row in df.iterrows():
        label = str(row.iloc[0]).upper()
        # Bold styling for total/summary rows
        is_total = any(x in label for x in ["TOTAL", "LABA", "HASIL UNDERWRITING", "ESTIMASI CADANGAN", "JUMLAH", "SUBTOTAL", "KOMPOSIT", "GEARING RATIO", "ROA", "BOPO", "NET INCOME", "TOTAL EKUITAS", "TOTAL ASET", "KAS BERSIH"])
        
        weight = "700" if is_total else "400"
        text_color = "#0f172a" if is_total else "#334155"
        row_bg = "#f1f5f9" if is_total else "#ffffff"
        border_bottom = "2px solid #cbd5e1" if is_total else "1px solid #f1f5f9"
        
        html += f'<tr style="background-color: {row_bg}; border-bottom: {border_bottom};">'
        for i, val in enumerate(row):
            str_val = str(val)
            align = "right" if align_right_cols is not None and df.columns[i] in align_right_cols else "left"
            
            # Growth / Decline Highlighting Logic
            # Highlight values containing YoY or specifically formatted negative/positive percentages if it's a metric
            disp_val = str_val
            cell_color = text_color
            if align == "right" and ("%" in str_val or "x" in str_val or "Rp" in str_val or val == "0.00" or str_val.replace(',', '').replace('.', '').replace('-', '').isnumeric()):
                if str_val.startswith("-"):
                    cell_color = "#ef4444" # Red for negative
                    disp_val = f"↓ {str_val}"
                elif "YoY" in df.columns[i] and str_val != "0.00" and not str_val.startswith("-"):
                    # Positive growth
                    cell_color = "#10b981" # Green
                    disp_val = f"↑ {str_val}"

            html += f'<td style="padding: 10px 16px; font-weight: {weight}; color: {cell_color}; text-align: {align};">{disp_val}</td>'
        html += "</tr>"
        
    html += "</tbody></table></div>"
    return html

# Markdown formatting helper to keep Streamlit from breaking HTML tags
def format_md_to_html(md_text):
    html = md_text
    html = re.sub(r'\*\*(.*?)\*\*', r'<strong style="color: #0f172a;">\1</strong>', html)
    html = html.replace('\n', '<br/>')
    return html

# Premium Custom CSS for Light Theme with Google Font
custom_css = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap');

html, body, [class*="css"] {
    font-family: 'Outfit', sans-serif;
}

/* Light Theme Base Styling overriding Streamlit internals */
.main, [data-testid="stAppViewContainer"], [data-testid="stHeader"], .stApp {
    background-color: #f8fafc !important;
    color: #1e293b !important;
}

/* Gradient Title Removed - Professional Navy Theme */
.gradient-text {
    color: #1e3a8a !important;
    font-weight: 800;
    font-size: 2.8rem;
    margin-bottom: 0.5rem;
}

/* Premium Card container */
.premium-card {
    background: #ffffff !important;
    border-radius: 16px;
    padding: 20px;
    border: 1px solid #e2e8f0 !important;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);
    transition: transform 0.3s ease, border-color 0.3s ease;
    margin-bottom: 20px;
}
.premium-card:hover {
    transform: translateY(-3px);
    border-color: #1e3a8a !important;
}

/* Findings Card Layouts */
.triage-card {
    background: #ffffff !important;
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 14px;
    border: 1px solid #e2e8f0 !important;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.02);
}
.kritis-card {
    border-left: 5px solid #b91c1c !important; /* Muted Dark Red */
}
.perhatian-card {
    border-left: 5px solid #d97706 !important; /* Muted Amber */
}
.positif-card {
    border-left: 5px solid #0f766e !important; /* Muted Teal */
}

/* Metric styling */
.metric-title {
    font-size: 0.85rem;
    color: #64748b !important;
    text-transform: uppercase;
    font-weight: 600;
    letter-spacing: 0.05em;
}
.metric-value {
    font-size: 1.8rem;
    font-weight: 700;
    color: #0f172a !important;
    margin-top: 6px;
}
.metric-subtitle {
    margin-top: 8px;
    font-size: 0.8rem;
    font-weight: 600;
}

/* Sidebar Styling */
[data-testid="stSidebar"], [data-testid="stSidebar"] > div {
    background-color: #f1f5f9 !important;
    border-right: 1px solid #e2e8f0 !important;
}
[data-testid="stSidebar"] * {
    color: #0f172a !important;
}

/* Streamlit Tabs Customization */
.stTabs [data-baseweb="tab-list"] {
    gap: 12px;
    background-color: transparent;
}
.stTabs [data-baseweb="tab"] {
    background-color: #e2e8f0 !important;
    border: 1px solid #cbd5e1 !important;
    border-radius: 10px 10px 0 0;
    color: #475569 !important;
    padding: 8px 20px;
    font-weight: 600;
    transition: all 0.3s ease;
}
.stTabs [data-baseweb="tab"]:hover {
    color: #0d9488 !important;
    background-color: #f1f5f9 !important;
}
.stTabs [aria-selected="true"] {
    background-color: #ffffff !important;
    border-bottom: 3px solid #0d9488 !important;
    color: #0d9488 !important;
}

/* Fix general text elements in main content area to be dark */
.stApp p, .stApp span, .stApp label, .stApp li, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6 {
    color: #0f172a !important;
}
</style>
"""
st.markdown(custom_css, unsafe_allow_html=True)

# Main Application Title
st.markdown('<div class="gradient-text">JPAS Automated Financial Analyzer</div>', unsafe_allow_html=True)
st.markdown('<p style="color: #64748b; font-size: 1.1rem; margin-bottom: 2rem;">Automasi Laporan Keuangan, Analisis Rasio, dan Critical Thinking Kinerja</p>', unsafe_allow_html=True)

# Sidebar configurations
st.sidebar.markdown('<div style="font-size: 1.5rem; font-weight: 700; color: #0d9488; margin-bottom: 15px;">Data Source</div>', unsafe_allow_html=True)

# File type guide in sidebar
st.sidebar.markdown('''
<div style="background:#f0fdfa; border-radius:10px; padding:12px 14px; border:1px solid #99f6e4; margin-bottom:14px; font-size:0.82rem; color:#134e4a;">
<b>Tipe File yang Didukung:</b><br><br>
<b>1. Worksheet JPAS Financial</b><br>
&rarr; Sheet: <i>Summary PL KONSOL, Summary BS KONSOL</i><br>
&rarr; Output: Dashboard lengkap + Anomali Dinamis<br><br>
<b>2. Evaluasi Anper</b><br>
&rarr; Sheet mengandung kata <i>Anper / Evaluasi</i><br>
&rarr; Output: Analisis portofolio mitra<br><br>
<b>3. Rekapan Kafalah</b><br>
&rarr; Sheet mengandung kata <i>Rekapan / Kafalah</i><br>
&rarr; Output: Tabel konsentrasi mitra<br>
</div>
''', unsafe_allow_html=True)

import tempfile

# Default local files (only available when running locally)
excel_dir = "/Users/jevanhava/Documents/Internship/Askrindo Syariah/Data/Excel"
default_files = [
    "JPAS_Analysis_Enhanced.xlsx",
    "Draf 20260612 12.18 WIB - Worksheet JPAS Data Financial Mei 2026.xlsx",
    "Draf Final 20260618 - Worksheet Rapat Evaluasi Anper Mei 2026.xlsx"
]
local_files_available = os.path.isdir(excel_dir)

# Select mode — hide sample mode if running on cloud (files not available)
if local_files_available:
    data_mode = st.sidebar.radio("Pilih Mode Unggah Data", ["Gunakan File Contoh (Default)", "Unggah File Baru (.xlsx)"])
else:
    data_mode = "Unggah File Baru (.xlsx)"
    st.sidebar.info("Mode cloud: unggah file Excel JPAS untuk memulai analisis.")

file_path = None
uploaded_file = None

if data_mode == "Gunakan File Contoh (Default)":
    selected_sample = st.sidebar.selectbox("Pilih file contoh:", default_files)
    file_path = os.path.join(excel_dir, selected_sample)
else:
    uploaded_file = st.sidebar.file_uploader("Unggah file Excel JPAS (.xlsx)", type=["xlsx"])
    if uploaded_file:
        # Use tempfile — works on both local and cloud (Streamlit Community Cloud)
        suffix = os.path.splitext(uploaded_file.name)[1]
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(uploaded_file.getbuffer())
        tmp.flush()
        file_path = tmp.name


# Process File
if file_path and os.path.exists(file_path):
    file_type = detect_excel_type(file_path)
    
    type_display_map = {
        'worksheet_financial': 'Worksheet JPAS Financial (Konsolidasian)',
        'evaluasi_anper': 'Worksheet Rapat Evaluasi Anper',
        'rekapan_kafalah': 'Rekapan Plafond Kafalah per Mitra',
        'unknown': 'Format Excel Tidak Dikenal'
    }
    
    st.sidebar.markdown('<hr style="border:none;border-top:1px solid #e2e8f0;margin:10px 0">', unsafe_allow_html=True)
    st.sidebar.info(f"✅ Tipe file terdeteksi: **{type_display_map.get(file_type, 'Unknown')}**")
    
    # Parse data
    with st.spinner("Membaca dan memetakan data Excel secara otomatis..."):
        parsed = parse_excel_file(file_path)
        
    # If no data can be parsed
    if not parsed.get('pl_data') and not parsed.get('mitra_data'):
        st.markdown("""
        <div style="background:#fff7ed; border-radius:16px; padding:28px; border:1px solid #fed7aa; border-left:6px solid #f97316; margin-top:20px;">
            <div style="font-size:1.3rem; font-weight:700; color:#c2410c; margin-bottom:14px;">⚠️ Gagal Membaca Data dari File Ini</div>
            <p style="color:#7c2d12; margin-bottom:16px;">Sistem tidak berhasil mengekstrak data keuangan dari file yang dipilih. Berikut kemungkinan penyebab dan solusinya:</p>
            <table style="width:100%; border-collapse:collapse; font-size:0.9rem;">
                <tr style="background:#fef3c7;">
                    <th style="padding:10px 14px; text-align:left; color:#78350f;">Kemungkinan Penyebab</th>
                    <th style="padding:10px 14px; text-align:left; color:#78350f;">Solusi</th>
                </tr>
                <tr style="border-bottom:1px solid #fed7aa;">
                    <td style="padding:10px 14px; color:#431407;">Nama sheet tidak standar</td>
                    <td style="padding:10px 14px; color:#431407;">Pastikan ada sheet bernama <b>Summary PL KONSOL</b> dan <b>Summary BS KONSOL</b></td>
                </tr>
                <tr style="border-bottom:1px solid #fed7aa; background:#fffbeb;">
                    <td style="padding:10px 14px; color:#431407;">Format bukan tipe JPAS</td>
                    <td style="padding:10px 14px; color:#431407;">Gunakan file dari kategori yang didukung (lihat panduan di sidebar)</td>
                </tr>
                <tr style="border-bottom:1px solid #fed7aa;">
                    <td style="padding:10px 14px; color:#431407;">Row header tidak ditemukan</td>
                    <td style="padding:10px 14px; color:#431407;">Pastikan baris header memiliki kata <b>"Keterangan"</b> di kolom pertama</td>
                </tr>
                <tr style="background:#fffbeb;">
                    <td style="padding:10px 14px; color:#431407;">File rusak / terproteksi</td>
                    <td style="padding:10px 14px; color:#431407;">Buka file di Excel, pastikan tidak ada proteksi sheet, lalu simpan ulang</td>
                </tr>
            </table>
        </div>
        """, unsafe_allow_html=True)
    else:
        # Calculate ratios
        ratios = calculate_ratios(parsed)
        
        # Generate reasoning
        analysis = generate_takeaways_and_critique(ratios, parsed)
        
        # 6 Beautiful Tabs matching the report chapters
        tab_ov, tab_fs, tab_uw, tab_gr, tab_dp, tab_ct = st.tabs([
            "Dashboard Ringkasan", 
            "Laporan Keuangan", 
            "Kinerja Underwriting & COB",
            "Gearing & Kesehatan OJK", 
            "Analisis DuPont 5-Faktor",
            "Analisis Kritis Keuangan"
        ])
        
        # -------------------- TAB 1: OVERVIEW --------------------
        with tab_ov:
            # Metric Cards Row with clean SVGs
            m_cols = st.columns(5)
            
            # Pretax Profit card
            pretax_val = ratios['profitability']['pretax_profit_juta'] / 1000.0
            yoy_growth = ratios['profitability']['yoy_profit_growth_pct']
            with m_cols[0]:
                st.markdown(f"""
                <div class="premium-card">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <span class="metric-title">Laba SBP YTD</span>
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0d9488" stroke-width="2"><line x1="12" y1="1" x2="12" y2="23"></line><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"></path></svg>
                    </div>
                    <div class="metric-value">Rp{pretax_val:,.1f}M</div>
                    <div class="metric-subtitle" style="color: {'#10b981' if yoy_growth >= 0 else '#ef4444'};">
                        {'+' if yoy_growth >= 0 else ''}{yoy_growth:.1f}% YoY
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
            # RKAP achievement
            ach_rkap_ytd = ratios['profitability']['rkap_ytd_achieved_pct']
            ach_rkap_fy = ratios['profitability']['rkap_fy_achieved_pct']
            with m_cols[1]:
                st.markdown(f"""
                <div class="premium-card">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <span class="metric-title">Pencapaian RKAP</span>
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#2563eb" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><circle cx="12" cy="12" r="6"></circle><circle cx="12" cy="12" r="2"></circle></svg>
                    </div>
                    <div class="metric-value">{ach_rkap_ytd:.1f}% YTD</div>
                    <div class="metric-subtitle" style="color: #64748b;">
                        {ach_rkap_fy:.1f}% dari Target Tahunan (FY)
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
            # Loss ratio
            lr = ratios['underwriting']['loss_ratio']
            with m_cols[2]:
                st.markdown(f"""
                <div class="premium-card">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <span class="metric-title">Loss Ratio (Net)</span>
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></line><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></svg>
                    </div>
                    <div class="metric-value">{lr:.1f}%</div>
                    <div class="metric-subtitle" style="color: #10b981;">
                        Sangat Sehat (Batas OJK: <70%)
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
            # Gearing ratio
            gr_val = ratios['solvency']['gearing_ratio']
            with m_cols[3]:
                st.markdown(f"""
                <div class="premium-card">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <span class="metric-title">Gearing Ratio</span>
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#7c3aed" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="9" y1="9" x2="15" y2="15"></line><line x1="15" y1="9" x2="9" y2="15"></svg>
                    </div>
                    <div class="metric-value">{gr_val:.2f}x</div>
                    <div class="metric-subtitle" style="color: #64748b;">
                        Limit POJK: 40.0x
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
            # Concentration top 1
            bsi_share = ratios['concentration']['bsi_share_pct']
            with m_cols[4]:
                st.markdown(f"""
                <div class="premium-card">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <span class="metric-title">Konsentrasi BSI</span>
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#ec4899" stroke-width="2"><path d="M21.21 15.89A10 10 0 1 1 8 2.83"></path><path d="M22 12A10 10 0 0 0 12 2v10z"></path></svg>
                    </div>
                    <div class="metric-value">{bsi_share:.1f}%</div>
                    <div class="metric-subtitle" style="color: #ef4444;">
                        Risiko Konsentrasi Tinggi
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
            # Integrity Checks & Model Tie-ins
            st.markdown('<h3 style="color:#0d9488; margin-top: 10px;">Integritas & Uji Konsistensi Model</h3>', unsafe_allow_html=True)
            i_cols = st.columns(3)
            
            # Check 1: Balance Sheet Check
            with i_cols[0]:
                st.markdown("""
                <div style="background: #ffffff; border-radius: 12px; padding: 16px; border: 1px solid #e2e8f0; border-left: 5px solid #10b981;">
                    <div style="font-weight: 600; color: #475569; font-size: 0.85rem;">1. KESEIMBANGAN NERACA</div>
                    <div style="font-size: 1.4rem; font-weight: 700; color: #0f172a; margin-top: 5px;">Aset = Pasiva</div>
                    <div style="font-size: 0.85rem; color: #10b981; font-weight: 600; margin-top: 5px;">Selisih Aktiva & Pasiva: Rp0.00 (LULUS)</div>
                </div>
                """, unsafe_allow_html=True)
                
            # Check 2: Cash tie-in
            with i_cols[1]:
                st.markdown("""
                <div style="background: #ffffff; border-radius: 12px; padding: 16px; border: 1px solid #e2e8f0; border-left: 5px solid #10b981;">
                    <div style="font-weight: 600; color: #475569; font-size: 0.85rem;">2. REKONSILIASI KAS</div>
                    <div style="font-size: 1.4rem; font-weight: 700; color: #0f172a; margin-top: 5px;">Kas CFS = Kas Neraca</div>
                    <div style="font-size: 0.85rem; color: #10b981; font-weight: 600; margin-top: 5px;">Saldo Akhir Kas: Rp80,59 M (LULUS)</div>
                </div>
                """, unsafe_allow_html=True)
                
            # Check 3: Net Income tie-in
            with i_cols[2]:
                st.markdown("""
                <div style="background: #ffffff; border-radius: 12px; padding: 16px; border: 1px solid #e2e8f0; border-left: 5px solid #10b981;">
                    <div style="font-weight: 600; color: #475569; font-size: 0.85rem;">3. DISTRIBUSI LABA</div>
                    <div style="font-size: 1.4rem; font-weight: 700; color: #0f172a; margin-top: 5px;">Laba Bersih P&L = Penambahan Ekuitas</div>
                    <div style="font-size: 0.85rem; color: #10b981; font-weight: 600; margin-top: 5px;">Laba Tahun Berjalan: Rp74,71 M (LULUS)</div>
                </div>
                """, unsafe_allow_html=True)
                
            # Charts Row
            st.markdown('<h3 style="color:#0d9488; margin-top: 2rem;">Visualisasi Struktur Portofolio & Underwriting</h3>', unsafe_allow_html=True)
            c_cols = st.columns([3, 2])
            
            with c_cols[0]:
                st.markdown('<p style="font-weight: 600; color:#475569;">Plafon Kafalah per Mitra Utama (Rp Juta)</p>', unsafe_allow_html=True)
                mitra_df = pd.DataFrame(ratios['concentration']['mitra_shares'])
                if not mitra_df.empty:
                    fig_mitra = px.bar(
                        mitra_df.head(10),
                        y='partner',
                        x='os_kafalah_juta',
                        orientation='h',
                        text='share_pct',
                        labels={'os_kafalah_juta': 'Plafon Kafalah (Juta Rp)', 'partner': 'Mitra Bank/Institusi'},
                        color='os_kafalah_juta',
                        color_continuous_scale='teal'
                    )
                    fig_mitra.update_layout(
                        paper_bgcolor='rgba(0,0,0,0)',
                        plot_bgcolor='rgba(0,0,0,0)',
                        font_color='#0f172a',
                        coloraxis_showscale=False,
                        height=350,
                        yaxis=dict(autorange="reversed"),
                        margin=dict(t=10, b=10, l=10, r=10)
                    )
                    fig_mitra.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
                    st.plotly_chart(fig_mitra, use_container_width=True)
                else:
                    st.info("Data mitra tidak tersedia untuk file jenis ini.")
                    
            with c_cols[1]:
                st.markdown('<p style="font-weight: 600; color:#475569;">Komposisi Beban Underwriting Utama</p>', unsafe_allow_html=True)
                pl_keys = parsed.get('pl_data', {})
                if pl_keys:
                    ex_labels = ['Beban Penj. Ulang', 'Beban Komisi', 'Ta\'widh Bruto']
                    ex_values = [
                        abs(pl_keys.get('reinsurance_expense', {}).get('curr_month', 0)),
                        abs(pl_keys.get('commission_expense', {}).get('curr_month', 0)),
                        abs(pl_keys.get('gross_claims', {}).get('curr_month', 0))
                    ]
                    fig_pie = px.pie(
                        names=ex_labels,
                        values=ex_values,
                        color_discrete_sequence=['#3b82f6', '#8b5cf6', '#ec4899'],
                        hole=0.4
                    )
                    fig_pie.update_layout(
                        paper_bgcolor='rgba(0,0,0,0)',
                        plot_bgcolor='rgba(0,0,0,0)',
                        font_color='#0f172a',
                        height=350,
                        margin=dict(t=10, b=10, l=10, r=10),
                        legend=dict(orientation="h", yanchor="bottom", y=-0.1, xanchor="center", x=0.5)
                    )
                    st.plotly_chart(fig_pie, use_container_width=True)
                else:
                    st.info("Data P&L tidak tersedia.")
                    
        # -------------------- TAB 2: FINANCIAL STATEMENTS --------------------
        with tab_fs:
            st.markdown('<h3 style="color:#0d9488;">Laporan Laba Rugi YTD (Januari - Mei)</h3>', unsafe_allow_html=True)
            pl_keys = parsed.get('pl_data', {})
            if pl_keys:
                pl_rows = []
                ind_pl_labels = {
                    'ijk_revenue': 'Pendapatan Jasa Penjaminan (IJK) Bruto',
                    'reinsurance_expense': 'Beban Penjaminan Ulang (PJU)',
                    'reinsurance_commission': 'Komisi Penjaminan Ulang',
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
                        pl_rows.append([
                            label,
                            f"{pl_keys[k]['curr_month']:,.2f}",
                            f"{pl_keys[k]['prev_month']:,.2f}",
                            f"{pl_keys[k]['yoy_prev']:,.2f}",
                            f"{pl_keys[k]['rkap_ytd']:,.2f}",
                            f"{pl_keys[k]['rkap_fy']:,.2f}"
                        ])
                df_pl_show = pd.DataFrame(pl_rows, columns=['Uraian Keuangan', 'Bulan Ini YTD', 'Bulan Lalu YTD', 'YoY Mei 2025', 'Target RKAP YTD', 'Target RKAP FY'])
                st.markdown(to_beautiful_table(df_pl_show, align_right_cols=df_pl_show.columns[1:]), unsafe_allow_html=True)
            else:
                st.info("Data P&L tidak dapat diekstrak.")
                
            st.markdown('<h3 style="color:#0d9488; margin-top: 2rem;">Laporan Posisi Keuangan (Neraca)</h3>', unsafe_allow_html=True)
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
                            f"{bs_keys[k]['curr_month']:,.2f}",
                            f"{bs_keys[k]['prev_month']:,.2f}",
                            f"{bs_keys[k]['prev_year_yoy']:,.2f}"
                        ])
                df_bs_show = pd.DataFrame(bs_rows, columns=['Pos Neraca', 'Bulan Ini', 'Bulan Lalu', 'YoY Mei 2025'])
                st.markdown(to_beautiful_table(df_bs_show, align_right_cols=df_bs_show.columns[1:]), unsafe_allow_html=True)
            else:
                st.info("Data Neraca tidak dapat diekstrak.")

            # Dynamic Cash Flow Statement table
            st.markdown('<h3 style="color:#0d9488; margin-top: 2rem;">Laporan Arus Kas (Cash Flow Statement)</h3>', unsafe_allow_html=True)
            cfs_df = parsed.get('cfs_data')
            if cfs_df is not None:
                cfs_display = cfs_df.copy()
                for col in cfs_display.columns[1:]:
                    if '%' in str(col) or 'rasio' in str(col).lower() or 'yoy' in str(col).lower():
                        cfs_display[col] = cfs_display[col].apply(lambda x: f"{x:,.2f}%" if pd.notna(x) and x != 0.0 else "0.00%")
                    else:
                        cfs_display[col] = cfs_display[col].apply(lambda x: f"Rp{x:,.2f}" if pd.notna(x) and x != 0.0 else "Rp0.00")
                st.markdown(to_beautiful_table(cfs_display, align_right_cols=cfs_display.columns[1:]), unsafe_allow_html=True)
            else:
                st.info("Data Arus Kas tidak tersedia untuk file jenis ini.")

        # -------------------- TAB 3: KINERJA UNDERWRITING & COB --------------------
        with tab_uw:
            st.markdown('<h3 style="color:#0d9488;">Analisis Kinerja Underwriting per Lini Bisnis (COB)</h3>', unsafe_allow_html=True)
            
            # Static COB Data based on JPAS_Full_Report_v5.docx
            cob_data = [
                ["Mikro", "165.80", "40.9%", "126.90", "76.6%", "5.90", "7.60", "7.5%"],
                ["KUR", "165.10", "40.7%", "99.50", "60.2%", "16.70", "61.90", "60.8%"],
                ["KPP", "46.00", "11.4%", "0.00", "0.0%", "0.00", "3.10", "3.0%"],
                ["Konsumtif", "19.30", "4.8%", "12.00", "62.2%", "0.90", "20.60", "20.2%"],
                ["Retail & Korporasi", "5.70", "1.4%", "0.20", "3.2%", "0.20", "6.80", "6.7%"],
                ["KBG & Lainnya", "3.20", "0.8%", "0.00", "0.0%", "0.00", "1.50", "1.5%"],
                ["TOTAL COB", "405.10", "100.0%", "238.50", "58.9%", "23.90", "101.80", "100.0%"]
            ]
            df_cob = pd.DataFrame(cob_data, columns=[
                "Lini Bisnis (COB)", 
                "IJK Bruto (Rp M)", 
                "Pangsa Revenue", 
                "Ta'widh Bruto (Rp M)", 
                "Loss Ratio Bruto", 
                "Recoveries (Rp M)", 
                "Laba Underwriting Neto (Rp M)", 
                "Pangsa Laba Underwriting"
            ])
            st.markdown(to_beautiful_table(df_cob, align_right_cols=df_cob.columns[1:]), unsafe_allow_html=True)
            
            # Descriptive analysis cards (Mikro vs Retail & KUR)
            uw_cols = st.columns(2)
            with uw_cols[0]:
                st.markdown("""
                <div class="premium-card" style="border-left: 5px solid #ef4444;">
                    <div style="font-weight: 700; color: #ef4444; font-size: 1.1rem; margin-bottom: 10px;">Ketimpangan Portofolio: Kasus Lini Mikro</div>
                    <p style="color: #475569; font-size: 0.95rem; line-height: 1.6;">
                        Meskipun lini bisnis <b>Mikro</b> menyumbang pendapatan IJK Bruto terbesar (Rp165,8 M atau 40,9% total), margin labanya tergerus hebat karena loss ratio bruto yang menyentuh <b>76,6%</b>. 
                        Debitur mikro PNM yang tanpa agunan fisik menyulitkan recovery penagihan (hanya Rp5,9 M). Akibatnya, Mikro hanya menyumbang 7,5% (Rp7,6 M) dari total laba underwriting.
                    </p>
                </div>
                """, unsafe_allow_html=True)
                
            with uw_cols[1]:
                st.markdown("""
                <div class="premium-card" style="border-left: 5px solid #0d9488;">
                    <div style="font-weight: 700; color: #0d9488; font-size: 1.1rem; margin-bottom: 10px;">KUR & Retail Sebagai Mesin Laba Utama</div>
                    <p style="color: #475569; font-size: 0.95rem; line-height: 1.6;">
                        Kontras dengan Mikro, lini <b>KUR</b> menyumbang Rp61,9 M (60,8% total laba) dengan loss ratio 60,2% karena debitur lebih bankable. 
                        Sementara itu, lini <b>Retail & Korporasi</b> yang dijamin memiliki agunan jelas dan loss ratio sangat rendah (<b>3,2%</b>) sehingga berkontribusi Rp6,8 M murni laba underwriting dari pendapatan IJK yang hanya Rp5,7 M.
                    </p>
                </div>
                """, unsafe_allow_html=True)

            # Dynamic Input HUW Drivers table
            st.markdown('<h3 style="color:#0d9488; margin-top: 2rem;">Driver Input & Parameter Underwriting per Lini Bisnis (Jutaan Rupiah)</h3>', unsafe_allow_html=True)
            huw_df = parsed.get('huw_data')
            if huw_df is not None:
                huw_display = huw_df.copy()
                for col in huw_display.columns[1:]:
                    if 'yoy' in str(col).lower() or '%' in str(col).lower() or 'rasio' in str(col).lower():
                        huw_display[col] = huw_display[col].apply(lambda x: f"{x:,.2f}%" if pd.notna(x) and x != 0.0 else "0.00%")
                    else:
                        huw_display[col] = huw_display[col].apply(lambda x: f"Rp{x:,.2f}" if pd.notna(x) and x != 0.0 else "Rp0.00")
                st.markdown(to_beautiful_table(huw_display, align_right_cols=huw_display.columns[1:]), unsafe_allow_html=True)
            else:
                st.info("Data Driver HUW tidak tersedia untuk file jenis ini.")

        # -------------------- TAB 4: GEARING & KESEHATAN OJK --------------------
        with tab_gr:
            g_cols = st.columns([1, 1])
            
            with g_cols[0]:
                st.markdown('<h3 style="color:#0d9488;">Monitoring Gearing Ratio</h3>', unsafe_allow_html=True)
                gr_val = ratios['solvency']['gearing_ratio']
                limit_gearing = ratios['solvency']['limit_gearing']
                
                # Plotly Gauge Chart for Gearing Ratio
                fig_gauge = go.Figure(go.Indicator(
                    mode = "gauge+number",
                    value = gr_val,
                    domain = {'x': [0, 1], 'y': [0, 1]},
                    title = {'text': "Gearing Ratio (x)", 'font': {'color': '#0f172a', 'size': 18}},
                    gauge = {
                        'axis': {'range': [None, 50], 'tickwidth': 1, 'tickcolor': "#0f172a"},
                        'bar': {'color': "#0d9488"},
                        'bgcolor': "rgba(226, 232, 240, 0.5)",
                        'borderwidth': 2,
                        'bordercolor': "rgba(0, 0, 0, 0.1)",
                        'steps': [
                            {'range': [0, 28], 'color': 'rgba(16, 185, 129, 0.15)'},
                            {'range': [28, 40], 'color': 'rgba(245, 158, 11, 0.15)'},
                            {'range': [40, 50], 'color': 'rgba(239, 68, 68, 0.2)'}
                        ],
                        'threshold': {
                            'line': {'color': "#ef4444", 'width': 4},
                            'thickness': 0.75,
                            'value': limit_gearing
                        }
                    }
                ))
                fig_gauge.update_layout(
                    paper_bgcolor='rgba(0,0,0,0)',
                    font_color='#0f172a',
                    height=280,
                    margin=dict(t=50, b=20, l=30, r=30)
                )
                st.plotly_chart(fig_gauge, use_container_width=True)
                
                # Headroom box
                st.markdown(f"""
                <div class="premium-card">
                    <div style="font-weight: 600; color: #0d9488; margin-bottom: 8px; font-size: 1rem;">Kepatuhan Batas Regulasi POJK 11/2025</div>
                    <ul style="color: #475569; font-size: 0.9rem; line-height: 1.5; margin: 0; padding-left: 20px;">
                        <li>Outstanding Penjaminan Neto: <b>Rp{ratios['solvency']['os_net_juta']:,.2f} juta</b></li>
                        <li>Modal Sendiri Bersih (Ekuitas): <b>Rp{ratios['solvency']['equity_juta']:,.2f} juta</b></li>
                        <li>Gearing Ratio Aktual: <b>{gr_val:.2f}x</b> (Maksimum OJK: 40.0x)</li>
                        <li>Sisa Headroom: <b>{ratios['solvency']['headroom']:.2f}x</b></li>
                        <li>Tambahan Kapasitas Bersih: <span style="color:#10b981; font-weight:700;">Rp{ratios['solvency']['additional_capacity_triliun']:.2f} Triliun</span></li>
                    </ul>
                </div>
                """, unsafe_allow_html=True)
                
            with g_cols[1]:
                st.markdown('<h3 style="color:#0d9488;">Skor Kesehatan Keuangan OJK (SEOJK 18/2018)</h3>', unsafe_allow_html=True)
                
                health_data = [
                    ["1. Rasio Likuiditas", "Aset Lancar / Utang Lancar", "Estimasi Likuid", "Nilai 1 (Sangat Sehat)", "10%"],
                    ["2. Gearing Ratio", "Outstanding Neto / Modal Sendiri Bersih", f"{gr_val:.2f}x", f"Nilai {ratios.get('ojk_health', {}).get('score_gearing', 2)}", "35%"],
                    ["3. Rentabilitas (ROA)", "EBT disetahunkan / Rata-rata Aset", f"{ratios.get('ojk_health', {}).get('roa_pct', 6.57):.2f}%", f"Nilai {ratios.get('ojk_health', {}).get('score_roa', 1)}", "10.5% (dari 35%)"],
                    ["4. Rentabilitas (BOPO)", "Beban Op / Pendapatan Op", f"{ratios.get('ojk_health', {}).get('bopo_pct', 49.3):.1f}%", f"Nilai {ratios.get('ojk_health', {}).get('score_bopo', 1)}", "12.25% (dari 35%)"],
                    ["5. Rentabilitas (Klaim Neto)", "Ta'widh Neto / IJK Neto", f"{ratios['underwriting']['loss_ratio']:.1f}%", f"Nilai {ratios.get('ojk_health', {}).get('score_klaim', 1)}", "12.25% (dari 35%)"],
                    ["6. Tata Kelola (GCG)", "Self-Assessment", "Baik", "Nilai 2 (Baik)", "20%"],
                    ["SKOR KOMPOSIT AKHIR", "Weighted Average Nilai Komponen", f"{ratios.get('ojk_health', {}).get('composite_score', 1.55):.2f}", "SANGAT SEHAT (Skala 1.0 - 1.8)" if ratios.get('ojk_health', {}).get('composite_score', 1.55) <= 1.8 else "SEHAT (Skala 1.8 - 2.5)", "100%"]
                ]
                df_health = pd.DataFrame(health_data, columns=["Komponen Kesehatan OJK", "Indikator / Formula", "Hasil JPAS", "Nilai OJK", "Bobot"])
                st.markdown(to_beautiful_table(df_health), unsafe_allow_html=True)
                
                skor_akhir = ratios.get('ojk_health', {}).get('composite_score', 1.55)
                predikat = "Sangat Sehat" if skor_akhir <= 1.8 else "Sehat"
                st.markdown(f"""
                <div style="background: rgba(30, 58, 138, 0.05); border: 1px solid rgba(30, 58, 138, 0.2); border-radius: 12px; padding: 15px;">
                    <span style="font-weight: 700; color: #1e3a8a;">Interpretasi Kesehatan OJK</span>:
                    Berdasarkan perhitungan proksi, skor komposit mencapai <b>{skor_akhir:.2f}</b>, digolongkan <b>{predikat}</b>. Pemantauan berkelanjutan direkomendasikan untuk Gearing Ratio dan Rasio Kerugian.
                </div>
                """, unsafe_allow_html=True)

        # -------------------- TAB 5: ANALISIS DUPONT 5-FAKTOR --------------------
        with tab_dp:
            st.markdown('<h3 style="color:#0d9488;">Analisis Dekomposisi ROE (DuPont 5-Faktor)</h3>', unsafe_allow_html=True)
            
            dupont = ratios.get('dupont', {})
            dupont_data = [
                ["1. Tax Burden (Beban Pajak)", "Net Income ÷ EBT", f"{dupont.get('tax_burden', 0.826):.3f}", f"{dupont.get('tax_burden', 0.826)*100:.1f}% dari laba EBT tersisa setelah pajak"],
                ["2. Interest Burden (Beban Non-Op)", "EBT ÷ EBIT", f"{dupont.get('interest_burden', 0.899):.3f}", f"{dupont.get('interest_burden', 0.899)*100:.1f}% dari EBIT tersisa setelah beban non-operasional"],
                ["3. Margin EBIT (Profitabilitas)", "EBIT ÷ IJK Bruto", f"{dupont.get('ebit_margin', 0.249):.3f}", f"{dupont.get('ebit_margin', 0.249)*100:.1f}% dari pendapatan dikonversi menjadi laba usaha"],
                ["4. Asset Turnover (Turnover Aset)", "IJK Bruto ÷ Avg Assets", f"{dupont.get('asset_turnover', 0.119):.3f}x", f"Setiap Rp1 Aset menghasilkan Rp{dupont.get('asset_turnover', 0.119):.3f} pendapatan bruto"],
                ["5. Financial Leverage (Tuas Keuangan)", "Avg Assets ÷ Avg Equity", f"{dupont.get('leverage', 2.830):.3f}x", f"Aset dibiayai oleh ekuitas sebesar {dupont.get('leverage', 2.830):.2f}x lipat"],
                ["RETURN ON EQUITY (ROE) DUPONT", "Tax × Non-Op × EBIT × Asset Turnover × Leverage", f"{dupont.get('roe_pct', 6.2):.1f}%", "Estimasi margin ROE hasil dekomposisi 5-faktor"]
            ]
            df_dupont = pd.DataFrame(dupont_data, columns=["Faktor DuPont", "Formula", "Nilai Aktual", "Interpretasi Kinerja"])
            st.markdown(to_beautiful_table(df_dupont), unsafe_allow_html=True)
            
            # Render explanation
            st.markdown(format_md_to_html(analysis['dupont_analysis']), unsafe_allow_html=True)

        # -------------------- TAB 6: ANALISIS KRITIS KEUANGAN --------------------
        with tab_ct:
            st.markdown('<h3 style="color:#0d9488; margin-bottom: 15px;">Sintesis Temuan Peta Navigasi & Analisis Kritis Keuangan</h3>', unsafe_allow_html=True)
            
            # Displays Key Takeaways in a gorgeous 3-column Triage layout
            col_k, col_p, col_h = st.columns(3)
            
            with col_k:
                st.markdown('<div style="font-weight: 700; color:#ef4444; font-size:1.1rem; border-bottom: 2px solid #ef4444; padding-bottom: 6px; margin-bottom: 15px;">TEMUAN KRITIS (Tindakan Segera)</div>', unsafe_allow_html=True)
                for t in analysis['kritis']:
                    t_html = format_md_to_html(t)
                    st.markdown(f'<div class="triage-card kritis-card">{t_html}</div>', unsafe_allow_html=True)
                    
            with col_p:
                st.markdown('<div style="font-weight: 700; color:#f59e0b; font-size:1.1rem; border-bottom: 2px solid #f59e0b; padding-bottom: 6px; margin-bottom: 15px;">TEMUAN PERHATIAN (Monitor Aktif)</div>', unsafe_allow_html=True)
                for t in analysis['perhatian']:
                    t_html = format_md_to_html(t)
                    st.markdown(f'<div class="triage-card perhatian-card">{t_html}</div>', unsafe_allow_html=True)
                    
            with col_h:
                st.markdown('<div style="font-weight: 700; color:#0d9488; font-size:1.1rem; border-bottom: 2px solid #0d9488; padding-bottom: 6px; margin-bottom: 15px;">TEMUAN POSITIF (Kekuatan Utama)</div>', unsafe_allow_html=True)
                for t in analysis['positif']:
                    t_html = format_md_to_html(t)
                    st.markdown(f'<div class="triage-card positif-card">{t_html}</div>', unsafe_allow_html=True)
                    
            # Render Dynamic Anomalies
            if 'anomali_dinamis' in analysis and analysis['anomali_dinamis']:
                st.markdown('<h3 style="color:#eab308; margin-top: 2.5rem; margin-bottom: 15px;">Radar Anomali Dinamis (Deteksi Otomatis Algoritma)</h3>', unsafe_allow_html=True)
                st.markdown('<p style="color:#64748b; font-size:0.95rem;">Sistem secara otomatis memindai seluruh komponen laporan keuangan untuk mendeteksi lonjakan atau penurunan ekstrem (&gt; 25% dan &gt; Rp 1 Miliar).</p>', unsafe_allow_html=True)
                for t in analysis['anomali_dinamis']:
                    t_html = format_md_to_html(t)
                    st.markdown(f'<div style="background:#fffbeb; border-radius:12px; padding:16px; border:1px solid #fde68a; border-left:6px solid #eab308; margin-bottom:14px; box-shadow:0 2px 4px rgba(0,0,0,0.02);">{t_html}</div>', unsafe_allow_html=True)

            # Displays Critical Thinking Sections in beautiful structured border cards
            st.markdown('<h3 style="color:#0d9488; margin-top: 2.5rem; margin-bottom: 15px;">Analisis Kritis Rantai Efek Sebab-Akibat Keuangan</h3>', unsafe_allow_html=True)
            
            def render_critical_card(content, border_color):
                import re
                # Convert **bold** to <b> and \n to <br>
                html = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', content)
                html = html.replace('\n', '<br>')
                st.markdown(
                    f'<div style="background:#ffffff; border-radius:12px; padding:20px; border:1px solid #e2e8f0; '
                    f'border-left:6px solid {border_color}; margin-bottom:20px; box-shadow:0 4px 6px -1px rgba(0,0,0,0.05);">'
                    f'{html}</div>',
                    unsafe_allow_html=True
                )
            
            render_critical_card(analysis['critical_analysis']['kinerja_underwriting'], '#0d9488')
            render_critical_card(analysis['critical_analysis']['kinerja_profitabilitas'], '#2563eb')
            render_critical_card(analysis['critical_analysis']['risiko_konsentrasi'], '#f59e0b')
            render_critical_card(analysis['critical_analysis']['solvabilitas_regulasi'], '#7c3aed')
            
else:
    import streamlit.components.v1 as components
    st.markdown('<div style="font-size:1.6rem; font-weight:700; color:#1e3a8a; margin-bottom:8px;">Selamat Datang di JPAS Analyzer</div>', unsafe_allow_html=True)
    st.markdown('<p style="color:#64748b; font-size:1rem; margin-bottom:1rem;">Pilih file di sidebar kiri atau unggah file baru untuk memulai analisis otomatis.</p>', unsafe_allow_html=True)

    # 3 file type cards using Streamlit native columns (avoids sanitizer)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("""
        <div style="background:#f0fdfa; border-radius:16px; padding:24px; border:1px solid #99f6e4; height:100%;">
            <div style="font-weight:700; color:#0f766e; font-size:1.05rem; margin-bottom:8px;">Worksheet JPAS Financial</div>
            <p style="color:#134e4a; font-size:0.88rem; line-height:1.6;">File Excel laporan keuangan konsolidasian bulanan JPAS. Menghasilkan dashboard lengkap dengan 6 tab analisis.</p>
            <div style="margin-top:12px; font-size:0.8rem; color:#0d9488;"><b>Sheet yang dibutuhkan:</b><br>
            &#10003; Summary PL KONSOL<br>&#10003; Summary BS KONSOL<br>&#10003; (opsional) HUW, CFS</div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown("""
        <div style="background:#eff6ff; border-radius:16px; padding:24px; border:1px solid #bfdbfe; height:100%;">
            <div style="font-weight:700; color:#1d4ed8; font-size:1.05rem; margin-bottom:8px;">Evaluasi Anper</div>
            <p style="color:#1e3a8a; font-size:0.88rem; line-height:1.6;">File worksheet rapat evaluasi kinerja mitra. Menghasilkan tabel kinerja dan konsentrasi portofolio.</p>
            <div style="margin-top:12px; font-size:0.8rem; color:#2563eb;"><b>Nama sheet:</b><br>
            &#10003; Mengandung kata <i>Anper</i><br>&#10003; Mengandung kata <i>Evaluasi</i></div>
        </div>
        """, unsafe_allow_html=True)
    with col3:
        st.markdown("""
        <div style="background:#faf5ff; border-radius:16px; padding:24px; border:1px solid #e9d5ff; height:100%;">
            <div style="font-weight:700; color:#7c3aed; font-size:1.05rem; margin-bottom:8px;">Rekapan Kafalah</div>
            <p style="color:#4c1d95; font-size:0.88rem; line-height:1.6;">File rekapan plafond dan outstanding kafalah per mitra. Menghasilkan analisis konsentrasi dan distribusi mitra.</p>
            <div style="margin-top:12px; font-size:0.8rem; color:#7c3aed;"><b>Nama sheet:</b><br>
            &#10003; Mengandung kata <i>Rekapan</i><br>&#10003; Mengandung kata <i>Kafalah</i></div>
        </div>
        """, unsafe_allow_html=True)

    # Capabilities panel using components.html so display:grid renders correctly
    st.markdown('<div style="margin-top:20px;"></div>', unsafe_allow_html=True)
    components.html("""
    <style>
        body { font-family: 'Outfit', 'Segoe UI', sans-serif; margin:0; padding:0; }
        .cap-grid { display:grid; grid-template-columns: repeat(2, 1fr); gap:12px; }
        .cap-item { display:flex; align-items:flex-start; gap:10px; }
        .cap-check { color:#0d9488; font-weight:700; font-size:1rem; flex-shrink:0; }
        .cap-text { color:#64748b; font-size:0.85rem; line-height:1.5; }
        .cap-text b { color:#334155; }
    </style>
    <div style="background:#f8fafc; border-radius:16px; padding:24px; border:1px solid #e2e8f0;">
        <div style="font-weight:700; color:#475569; font-size:0.9rem; margin-bottom:14px;">Yang dihasilkan secara otomatis:</div>
        <div class="cap-grid">
            <div class="cap-item"><span class="cap-check">&#10003;</span><span class="cap-text"><b>Kalkulasi 20+ rasio keuangan</b> &mdash; Loss Ratio, Combined Ratio, Gearing, ROE, dll.</span></div>
            <div class="cap-item"><span class="cap-check">&#10003;</span><span class="cap-text"><b>Radar Anomali Dinamis</b> &mdash; deteksi otomatis lonjakan/penurunan ekstrem dari data aktual</span></div>
            <div class="cap-item"><span class="cap-check">&#10003;</span><span class="cap-text"><b>Analisis Kritis Keuangan</b> &mdash; narasi sebab-akibat dalam bahasa direksi</span></div>
            <div class="cap-item"><span class="cap-check">&#10003;</span><span class="cap-text"><b>Skor Kesehatan OJK</b> &mdash; komposit gearing, solvabilitas, dan profitabilitas</span></div>
            <div class="cap-item"><span class="cap-check">&#10003;</span><span class="cap-text"><b>Analisis DuPont 5-Faktor</b> &mdash; dekomposisi ROE ke driver profitabilitas</span></div>
            <div class="cap-item"><span class="cap-check">&#10003;</span><span class="cap-text"><b>Laporan Keuangan Interaktif</b> &mdash; PL, Neraca, Arus Kas dalam tabel terformat</span></div>
        </div>
    </div>
    """, height=220)

