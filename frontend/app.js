let activeTab = 'ringkasan';
let hasData = false;
const defaultLiniBisnis = ["Mikro", "KUR", "KPP", "Konsumtif", "Retail & Korporasi", "KBG & Lainnya"];
let activeFilters = new Set(defaultLiniBisnis);
let notifications = [];

// Escape user-derived strings before inserting them into HTML
function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
}

// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
    // Load theme from localStorage
    const savedTheme = localStorage.getItem('theme') || 'light';
    const html = document.documentElement;
    const dot = document.getElementById('darkmode-toggle-dot');
    const btn = document.getElementById('darkmode-toggle-btn');
    if (savedTheme === 'dark') {
        html.classList.remove('light');
        html.classList.add('dark');
        if (dot) dot.className = 'w-4 h-4 bg-white rounded-full shadow transition-transform duration-200 translate-x-6';
        if (btn) btn.className = 'w-12 h-6 bg-primary rounded-full p-1 transition-colors duration-200 relative flex items-center';
    } else {
        html.classList.remove('dark');
        html.classList.add('light');
        if (dot) dot.className = 'w-4 h-4 bg-white rounded-full shadow transition-transform duration-200 translate-x-0';
        if (btn) btn.className = 'w-12 h-6 bg-outline-variant rounded-full p-1 transition-colors duration-200 relative flex items-center';
    }

    checkStatus();
    initFilterMenu();
});


// Show / Hide Loading Overlay
function showLoading(text) {
    document.getElementById('loading-text').textContent = text || 'Memuat data...';
    document.getElementById('loading-overlay').classList.remove('hidden');
}

function hideLoading() {
    document.getElementById('loading-overlay').classList.add('hidden');
}

// Fetch general server status
async function checkStatus() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        
        hasData = data.has_data;

        if (hasData) {
            // Update Active Files list (filenames are user-controlled — never inject raw)
            const filesContainer = document.getElementById('active-files-container');
            filesContainer.innerHTML = '';
            data.processed_files.forEach(f => {
                const el = document.createElement('div');
                el.className = 'flex items-center gap-1.5 text-on-surface-variant font-medium overflow-hidden text-ellipsis whitespace-nowrap';
                const check = document.createElement('span');
                check.className = 'text-primary font-bold';
                check.textContent = '✅';
                const name = document.createElement('span');
                name.textContent = f;
                el.append(check, name);
                filesContainer.appendChild(el);
            });

            // Show main dashboard
            document.getElementById('sidebar-navigation').classList.remove('hidden');
            document.getElementById('reset-btn').classList.remove('hidden');
            document.getElementById('welcome-screen').classList.add('hidden');
            document.getElementById('app-content').classList.remove('hidden');
            
            // Load dashboard data
            await loadDashboardData();
            switchTab(activeTab);
        } else {
            // Show welcome screen
            document.getElementById('sidebar-navigation').classList.add('hidden');
            document.getElementById('reset-btn').classList.add('hidden');
            document.getElementById('welcome-screen').classList.remove('hidden');
            document.getElementById('app-content').classList.add('hidden');
        }
    } catch (err) {
        console.error('Gagal mengambil status:', err);
    }
}

// Upload multiple files
async function uploadMultipleFiles(input) {
    if (!input.files || input.files.length === 0) return;
    
    const formData = new FormData();
    for (let i = 0; i < input.files.length; i++) {
        formData.append('files', input.files[i]);
    }
    
    showLoading(`Mengunggah dan menganalisis ${input.files.length} file...`);
    try {
        const res = await fetch('/api/upload-multiple', {
            method: 'POST',
            body: formData
        });
        const data = await res.json();
        
        if (!res.ok) {
            alert('Error: ' + (data.detail || 'Gagal memproses file'));
        } else {
            let msg = `Berhasil memproses ${data.uploaded.length} file.`;
            if (data.errors && data.errors.length > 0) {
                msg += '\nError:\n' + data.errors.join('\n');
            }
            alert(msg);
            await checkStatus();
        }
    } catch (err) {
        alert('Gagal mengunggah file.');
    } finally {
        input.value = ''; // clear input
        hideLoading();
    }
}

// Reset Session
async function resetSession() {
    if (!confirm('Apakah Anda yakin ingin menghapus data analisis saat ini?')) return;
    
    showLoading('Mereset sesi...');
    try {
        await fetch('/api/reset', { method: 'POST' });
        activeTab = 'ringkasan';
        await checkStatus();
    } catch (err) {
        alert('Gagal mereset sesi.');
    } finally {
        hideLoading();
    }
}

// Tab Switching Routing
function switchTab(tabId) {
    activeTab = tabId;
    
    // Toggle active link styling
    const tabs = ['ringkasan', 'laporan', 'underwriting', 'investasi', 'ojk', 'temuan'];
    tabs.forEach(t => {
        const navLink = document.getElementById('nav-' + t);
        const tabPane = document.getElementById('tab-' + t);
        
        if (t === tabId) {
            navLink.className = 'flex items-center gap-3 text-on-secondary-fixed bg-secondary-container border-l-4 border-primary px-4 py-3 rounded-r-lg font-semibold transition-all duration-200';
            navLink.querySelector('.material-symbols-outlined').classList.add('icon-fill');
            tabPane.classList.remove('hidden');
            tabPane.classList.add('flex', 'flex-col', 'gap-6');
        } else {
            navLink.className = 'flex items-center gap-3 text-on-surface-variant px-4 py-3 border-l-4 border-transparent hover:bg-surface-container-low transition-colors rounded-r-lg font-medium';
            navLink.querySelector('.material-symbols-outlined').classList.remove('icon-fill');
            tabPane.classList.add('hidden');
            tabPane.classList.remove('flex', 'flex-col', 'gap-6');
        }
    });
    
    // Re-apply search filter in the new active tab
    const searchInput = document.getElementById('header-search-input');
    if (searchInput) {
        searchActiveTab(searchInput.value);
    }
}

// Render the data-quality warnings banner (missing sheets, unavailable analyses)
function renderDataWarnings(warnings) {
    const banner = document.getElementById('data-warnings-banner');
    if (!banner) return;
    banner.innerHTML = '';
    if (!warnings || warnings.length === 0) {
        banner.classList.add('hidden');
        return;
    }
    banner.classList.remove('hidden');
    const title = document.createElement('div');
    title.className = 'font-semibold text-[#92400e] flex items-center gap-1.5 mb-1';
    title.innerHTML = '<span class="material-symbols-outlined text-[16px]">warning</span> Data Tidak Lengkap';
    banner.appendChild(title);
    warnings.forEach(w => {
        const line = document.createElement('div');
        line.className = 'text-[12px] text-[#92400e]/90 leading-relaxed';
        line.textContent = '• ' + w;
        banner.appendChild(line);
    });
}

// Load and populate ratios / insights
async function loadDashboardData() {
    try {
        const res = await fetch('/api/dashboard');
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            console.error('Error dashboard:', err.detail);
            renderDataWarnings(['Gagal memuat data dashboard: ' + (err.detail || res.status)]);
            return;
        }

        const data = await res.json();

        // 0. Data-quality warnings from the backend
        renderDataWarnings(data.data_warnings);

        // 1. Populate KPI Cards on Ringkasan
        document.getElementById('kpi-gearing-val').textContent = data.kpis.gearing.val;
        document.getElementById('kpi-gearing-status').innerHTML = `<span class="material-symbols-outlined text-[14px]">trending_flat</span><span>${data.kpis.gearing.status}</span>`;
        
        document.getElementById('kpi-loss-val').textContent = data.kpis.loss_ratio.val;
        const lossStatusEl = document.getElementById('kpi-loss-status');
        lossStatusEl.innerHTML = `<span class="material-symbols-outlined text-[14px]">trending_down</span><span>${data.kpis.loss_ratio.status}</span>`;
        lossStatusEl.className = data.kpis.loss_ratio.type === 'success' ? 'flex items-center gap-1 text-[11px] font-semibold text-primary' : 'flex items-center gap-1 text-[11px] font-semibold text-error';

        document.getElementById('kpi-roa-val').textContent = data.kpis.roa.val;
        const roaStatusEl = document.getElementById('kpi-roa-status');
        roaStatusEl.innerHTML = `<span class="material-symbols-outlined text-[14px]">${data.kpis.roa.type === 'success' ? 'trending_up' : 'trending_down'}</span><span>${data.kpis.roa.status}</span>`;
        roaStatusEl.className = data.kpis.roa.type === 'success' ? 'flex items-center gap-1 text-[11px] font-semibold text-primary' : 'flex items-center gap-1 text-[11px] font-semibold text-error';

        document.getElementById('kpi-profit-val').textContent = data.kpis.net_profit.val;
        const profitStatusEl = document.getElementById('kpi-profit-status');
        profitStatusEl.innerHTML = `<span class="material-symbols-outlined text-[14px]">${data.kpis.net_profit.status.startsWith('-') ? 'trending_down' : 'trending_up'}</span><span>${data.kpis.net_profit.status}</span>`;
        profitStatusEl.className = data.kpis.net_profit.type === 'success' ? 'flex items-center gap-1 text-[11px] font-semibold text-primary' : 'flex items-center gap-1 text-[11px] font-semibold text-error';

        document.getElementById('kpi-ijk-val').textContent = data.kpis.ijk_bruto.val;
        const ijkStatusEl = document.getElementById('kpi-ijk-status');
        ijkStatusEl.innerHTML = `<span class="material-symbols-outlined text-[14px]">${data.kpis.ijk_bruto.status.startsWith('-') ? 'trending_down' : 'trending_up'}</span><span>${data.kpis.ijk_bruto.status}</span>`;
        ijkStatusEl.className = data.kpis.ijk_bruto.type === 'success' ? 'flex items-center gap-1 text-[11px] font-semibold text-primary' : 'flex items-center gap-1 text-[11px] font-semibold text-error';

        // 2. Render Charts
        renderTrendChart(data.charts.trend_labels, data.charts.trend_data);
        renderPortfolioChart(data.charts.portfolio);

        // 3. Populate Laporan Keuangan Tables
        document.getElementById('table-pl-container').innerHTML = data.tables.pl;
        document.getElementById('table-bs-container').innerHTML = data.tables.bs;
        document.getElementById('table-cfs-container').innerHTML = data.tables.cfs;

        // 4. Populate Underwriting Tables
        document.getElementById('table-cob-container').innerHTML = data.tables.cob;
        document.getElementById('table-huw-container').innerHTML = data.tables.huw;
        // Mirror COB table into partner concentration panel
        const mitraEl = document.getElementById('table-mitra-concentration');
        if (mitraEl) mitraEl.innerHTML = data.tables.cob;

        // 4b. Underwriting KPI Cards (new mockup)
        const uwLossEl = document.getElementById('uw-loss-val');
        const uwProfitEl = document.getElementById('uw-profit-val');
        const uwIjkEl = document.getElementById('uw-ijk-val');
        const uwKlaimEl = document.getElementById('uw-klaim-val');
        if (uwLossEl) uwLossEl.innerHTML = `${data.kpis.loss_ratio?.val?.replace('%','') ?? '--'}<span class="text-xl text-on-surface-variant ml-1">%</span>`;
        if (uwProfitEl) uwProfitEl.textContent = data.kpis.net_profit?.val ?? '--';
        if (uwIjkEl) uwIjkEl.textContent = data.kpis.ijk_bruto?.val ?? '--';
        if (uwKlaimEl) uwKlaimEl.textContent = data.kpis.loss_ratio?.val ?? '--';

        // 5. Populate Investasi Solvabilitas details
        document.getElementById('solvency-os-net').textContent = data.solvency.os_net;
        document.getElementById('solvency-equity').textContent = data.solvency.equity;
        document.getElementById('solvency-capacity').textContent = data.solvency.capacity;

        // Update SVG gauge needle
        const gearingVal = data.solvency.gearing_val ?? 0;
        const gearingLimit = data.solvency.gearing_limit ?? 40.0;
        const gaugeRatio = Math.min(1, Math.max(0, gearingVal / (gearingLimit * 1.1))); // cap at 110% of limit
        const needleDeg = -90 + (gaugeRatio * 180); // -90deg (left) to +90deg (right)
        const needleEl = document.getElementById('gearing-gauge-needle');
        const arcEl = document.getElementById('gearing-gauge-arc');
        const radialValEl = document.getElementById('gearing-radial-val');
        const badgeEl = document.getElementById('gearing-action-badge');
        let gaugeColor = '#006565'; // green
        if (gearingVal > gearingLimit * 0.7) gaugeColor = '#d97706'; // amber warning
        if (gearingVal > gearingLimit) gaugeColor = '#ba1a1a'; // red breach
        if (needleEl) needleEl.setAttribute('transform', `rotate(${needleDeg} 50 50)`);
        if (arcEl) arcEl.setAttribute('stroke', gaugeColor);
        if (radialValEl) { radialValEl.textContent = gearingVal.toFixed(2) + 'x'; radialValEl.style.color = gaugeColor; }
        if (badgeEl) {
            const isBreaching = gearingVal > gearingLimit;
            badgeEl.textContent = isBreaching ? 'MELEBIHI BATAS' : 'AMAN';
            badgeEl.className = isBreaching
                ? 'px-2 py-0.5 bg-error/10 border border-error/30 rounded text-[11px] font-semibold text-error'
                : 'px-2 py-0.5 bg-primary/10 border border-primary/30 rounded text-[11px] font-semibold text-primary';
        }

        // Asset allocation donut (CSS conic-gradient)
        const donutEl = document.getElementById('investasi-donut-chart');
        const donutTotalEl = document.getElementById('investasi-donut-total');
        const sbsnEl = document.getElementById('investasi-sbsn-pct');
        const depositoEl = document.getElementById('investasi-deposito-pct');
        const reksadanaEl = document.getElementById('investasi-reksadana-pct');
        
        if (data.investasi) {
            if (donutTotalEl) donutTotalEl.textContent = data.investasi.total ?? '--';
            const sbsnPct = data.investasi.sbsn_pct ?? 0.0;
            const depositoPct = data.investasi.deposito_pct ?? 0.0;
            const reksadanaPct = data.investasi.reksadana_pct ?? 0.0;
            
            const sbsnPctFormatted = sbsnPct.toString().replace('.', ',');
            const depositoPctFormatted = depositoPct.toString().replace('.', ',');
            const reksadanaPctFormatted = reksadanaPct.toString().replace('.', ',');
            
            if (donutEl) {
                // Ensure proper conic-gradient stops matching colors used in legend
                donutEl.style.background = `conic-gradient(#006565 0% ${sbsnPct}%, #64748b ${sbsnPct}% ${sbsnPct + depositoPct}%, #cbd5e1 ${sbsnPct + depositoPct}% 100%)`;
            }
            if (sbsnEl) sbsnEl.textContent = `${sbsnPctFormatted}% (${data.investasi.sbsn_val})`;
            if (depositoEl) depositoEl.textContent = `${depositoPctFormatted}% (${data.investasi.deposito_val})`;
            if (reksadanaEl) reksadanaEl.textContent = `${reksadanaPctFormatted}% (${data.investasi.reksadana_val})`;
        } else {
            // No investment breakdown parsed — show empty state, never invented numbers
            if (donutTotalEl) donutTotalEl.textContent = '--';
            if (donutEl) donutEl.style.background = 'conic-gradient(#e6e8ea 0% 100%)';
            if (sbsnEl) sbsnEl.textContent = '--';
            if (depositoEl) depositoEl.textContent = '--';
            if (reksadanaEl) reksadanaEl.textContent = '--';
        }

        document.getElementById('table-health-container').innerHTML = data.tables.health;
        document.getElementById('table-dupont-container').innerHTML = data.tables.dupont;
        document.getElementById('dupont-narrative-container').innerHTML = data.cause_effect.dupont;

        // 6. Populate OJK compliance indicators & table
        // Missing/zero values render as N/A — never as an at-threshold "healthy" number
        const BADGE_OK = 'text-[10px] font-bold px-2 py-0.5 rounded border border-[#006565]/30 bg-[#006565]/10 text-primary';
        const BADGE_BAD = 'text-[10px] font-bold px-2 py-0.5 rounded border border-error/30 bg-error/10 text-error';
        const BADGE_NA = 'text-[10px] font-bold px-2 py-0.5 rounded border border-outline-variant bg-surface-container-low text-on-surface-variant';
        function setOjkIndicator(prefix, val, suffix, isHealthy) {
            const valEl = document.getElementById(`ojk-${prefix}-val`);
            const badge = document.getElementById(`ojk-${prefix}-badge`);
            const icon = document.getElementById(`ojk-${prefix}-icon`);
            if (!val) {
                if (valEl) valEl.textContent = 'N/A';
                if (badge) { badge.textContent = 'DATA TIDAK TERSEDIA'; badge.className = BADGE_NA; }
                if (icon) icon.setAttribute('stroke', '#6e7979');
                return;
            }
            const healthy = isHealthy(val);
            if (valEl) valEl.textContent = val.toLocaleString('id-ID', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + suffix;
            if (badge) { badge.textContent = healthy ? 'SEHAT' : 'TIDAK SEHAT'; badge.className = healthy ? BADGE_OK : BADGE_BAD; }
            if (icon) icon.setAttribute('stroke', healthy ? '#006565' : '#ba1a1a');
        }
        setOjkIndicator('cr', data.ratios?.ojk_padk47?.current_ratio, '%', v => v >= 100.0);
        setOjkIndicator('bopo', data.ratios?.ojk_padk47?.bopo_syariah, '%', v => v <= 90.0);
        setOjkIndicator('leverage', data.ratios?.ojk_padk47?.leverage_ratio_ojk, 'x', v => v <= 3.0);

        document.getElementById('table-ojk-container').innerHTML = data.tables.ojk;

        // 7. Populate AI Takeaways & Triage Cards
        populateTriageContainer('findings-critical-container', data.findings.kritis, 'kritis-card border-l-4 border-error bg-surface rounded-lg p-4 text-[13px] leading-relaxed shadow-sm border border-outline-variant');
        populateTriageContainer('findings-warning-container', data.findings.perhatian, 'perhatian-card border-l-4 border-[#d97706] bg-surface rounded-lg p-4 text-[13px] leading-relaxed shadow-sm border border-outline-variant');
        populateTriageContainer('findings-positive-container', data.findings.positif, 'positif-card border-l-4 border-[#006565] bg-surface rounded-lg p-4 text-[13px] leading-relaxed shadow-sm border border-outline-variant');

        // Dynamic anomalies
        populateTriageContainer('anomali-positif-container', data.findings.anomali_positif, 'positif-card border-l-4 border-[#006565] bg-surface rounded-lg p-4 text-[13px] leading-relaxed shadow-sm border border-outline-variant');
        populateTriageContainer('anomali-peringatan-container', data.findings.anomali_peringatan, 'perhatian-card border-l-4 border-[#d97706] bg-surface rounded-lg p-4 text-[13px] leading-relaxed shadow-sm border border-outline-variant');
        populateTriageContainer('anomali-negatif-container', data.findings.anomali_negatif, 'kritis-card border-l-4 border-error bg-surface rounded-lg p-4 text-[13px] leading-relaxed shadow-sm border border-outline-variant');

        // Cause-Effect Narratives
        document.getElementById('cause-effect-underwriting').innerHTML = data.cause_effect.underwriting;
        document.getElementById('cause-effect-profitability').innerHTML = data.cause_effect.profitability;
        document.getElementById('cause-effect-concentration').innerHTML = data.cause_effect.concentration;
        document.getElementById('cause-effect-solvency').innerHTML = data.cause_effect.solvency;

        // Populate notifications from findings
        notifications = [];
        if (data.findings && data.findings.kritis) {
            data.findings.kritis.forEach(k => notifications.push({ type: 'kritis', content: k }));
        }
        if (data.findings && data.findings.perhatian) {
            data.findings.perhatian.forEach(p => notifications.push({ type: 'perhatian', content: p }));
        }
        renderNotifications();

        // Apply active filters
        applyFilters();

    } catch (err) {
        console.error('Gagal mengambil data dashboard:', err);
        renderDataWarnings(['Gagal memuat data dashboard. Periksa koneksi ke server lalu muat ulang halaman.']);
    }
}

// Triage population helper
function populateTriageContainer(id, list, cardClass) {
    const container = document.getElementById(id);
    container.innerHTML = '';
    if (!list || list.length === 0) {
        container.innerHTML = '<p class="text-xs text-on-surface-variant italic">Tidak ada temuan terdeteksi.</p>';
        return;
    }
    list.forEach(item => {
        const card = document.createElement('div');
        card.className = cardClass;
        card.innerHTML = item;
        container.appendChild(card);
    });
}

// Chart.js controllers
let trendChart = null;
function renderTrendChart(labels, data) {
    const ctx = document.getElementById('trendChart').getContext('2d');
    if (trendChart) trendChart.destroy();
    trendChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'IJK Bruto (Miliar Rp)',
                data: data,
                borderColor: '#006565',
                backgroundColor: 'rgba(0, 101, 101, 0.04)',
                borderWidth: 3,
                fill: true,
                tension: 0.4,
                pointBackgroundColor: '#006565',
                pointRadius: 4,
                pointHoverRadius: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: '#6e7979', font: { family: 'Inter' } }
                },
                y: {
                    grid: { color: 'rgba(189, 201, 200, 0.25)' },
                    ticks: { color: '#6e7979', font: { family: 'Inter' }, callback: (val) => val.toLocaleString('id-ID') + ' M' }
                }
            }
        }
    });
}

let portfolioChart = null;
function renderPortfolioChart(values) {
    const ctx = document.getElementById('portfolioChart').getContext('2d');
    if (portfolioChart) portfolioChart.destroy();
    portfolioChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Sukuk Korporasi', 'Deposito Syariah', 'Reksadana Syariah'],
            datasets: [{
                data: values,
                backgroundColor: ['#006565', '#64748b', '#dae2fd'],
                borderWidth: 2,
                borderColor: '#ffffff'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '58%',
            plugins: {
                legend: { display: false }
            }
        }
    });
    
    // Inject Custom Legend matching Stitch
    const legendContainer = document.getElementById('portfolio-legend-container');
    legendContainer.innerHTML = `
        <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
                <div class="w-3 h-3 rounded-sm bg-[#006565]"></div>
                <span class="text-on-surface-variant font-medium">Sukuk Korporasi</span>
            </div>
            <span class="font-body-md font-semibold text-on-surface">${values[0]}%</span>
        </div>
        <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
                <div class="w-3 h-3 rounded-sm bg-[#64748b]"></div>
                <span class="text-on-surface-variant font-medium">Deposito Syariah</span>
            </div>
            <span class="font-body-md font-semibold text-on-surface">${values[1]}%</span>
        </div>
        <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
                <div class="w-3 h-3 rounded-sm bg-[#dae2fd]"></div>
                <span class="text-on-surface-variant font-medium">Reksadana Syariah</span>
            </div>
            <span class="font-body-md font-semibold text-on-surface">${values[2]}%</span>
        </div>
    `;
}

// ==========================================
//           UI FEATURES INTERFACES
// ==========================================

// 1. Search Active Tab (Client-side)
function searchActiveTab(query) {
    const q = query.toLowerCase().trim();
    const activePane = document.getElementById('tab-' + activeTab);
    if (!activePane) return;
    
    const tables = activePane.querySelectorAll('table');
    tables.forEach(table => {
        const rows = table.querySelectorAll('tbody tr');
        rows.forEach(row => {
            const cells = row.querySelectorAll('td');
            let found = false;
            cells.forEach(cell => {
                if (cell.textContent.toLowerCase().includes(q)) {
                    found = true;
                }
            });
            if (found || q === '') {
                row.style.display = '';
            } else {
                row.style.display = 'none';
            }
        });
    });
}

// 2. Export Data (Consolidated Excel Download)
function exportData() {
    window.location.href = '/api/export';
}

// 3. Notifications Menu & Badge
function toggleNotificationMenu(event) {
    if (event) event.stopPropagation();
    const menu = document.getElementById('notif-menu');
    menu.classList.toggle('hidden');
    
    // Close other dropdowns
    document.getElementById('profile-menu').classList.add('hidden');
    const filterMenu = document.getElementById('filter-menu');
    if (filterMenu) filterMenu.classList.add('hidden');
}

function clearNotifications() {
    notifications = [];
    renderNotifications();
}

function renderNotifications() {
    const container = document.getElementById('notif-list-container');
    const badge = document.getElementById('notif-badge');
    if (!container) return;
    
    container.innerHTML = '';
    if (notifications.length === 0) {
        container.innerHTML = '<p class="text-xs text-on-surface-variant italic p-2 text-center">Tidak ada notifikasi baru.</p>';
        if (badge) badge.classList.add('hidden');
        return;
    }
    
    if (badge) badge.classList.remove('hidden');
    notifications.forEach((notif) => {
        const item = document.createElement('div');
        item.className = `p-2.5 rounded border-b border-outline-variant/30 text-xs flex gap-2 items-start hover:bg-surface-container-low transition-colors ${
            notif.type === 'kritis' ? 'bg-error/5 text-error font-medium' : 'bg-warning/5 text-[#d97706] font-medium'
        }`;
        // Strip markdown and HTML tags for notification preview
        const cleanContent = notif.content.replace(/<[^>]*>/g, '').replace(/\*\*/g, '');
        item.innerHTML = `
            <span class="material-symbols-outlined text-[16px] mt-0.5 ${notif.type === 'kritis' ? 'text-error' : 'text-[#d97706]'}">
                ${notif.type === 'kritis' ? 'error' : 'warning'}
            </span>
            <div class="flex-1">
                <div class="font-bold mb-0.5 text-on-surface">${notif.type === 'kritis' ? 'Kritis' : 'Perhatian'}</div>
                <div class="text-[11px] leading-relaxed text-on-surface-variant font-normal">${cleanContent}</div>
            </div>
        `;
        container.appendChild(item);
    });
}

// 4. Profile Dropdown Menu
function toggleProfileMenu(event) {
    if (event) event.stopPropagation();
    const menu = document.getElementById('profile-menu');
    menu.classList.toggle('hidden');
    
    // Close other dropdowns
    document.getElementById('notif-menu').classList.add('hidden');
    const filterMenu = document.getElementById('filter-menu');
    if (filterMenu) filterMenu.classList.add('hidden');
}

// 5. Support Modal Form
function openSupportModal() {
    document.getElementById('support-modal').classList.remove('hidden');
}

function closeSupportModal() {
    document.getElementById('support-modal').classList.add('hidden');
}

function submitSupportForm(event) {
    event.preventDefault();
    alert('Tiket support berhasil dibuat! Tim support Askrindo Syariah akan menghubungi Anda.');
    closeSupportModal();
    event.target.reset();
}

// 6. Settings Modal (Dark Mode, Auto-Refresh)
function openSettingsModal() {
    document.getElementById('settings-modal').classList.remove('hidden');
}

function closeSettingsModal() {
    document.getElementById('settings-modal').classList.add('hidden');
}

function toggleDarkMode() {
    const html = document.documentElement;
    const dot = document.getElementById('darkmode-toggle-dot');
    const btn = document.getElementById('darkmode-toggle-btn');
    
    if (html.classList.contains('dark')) {
        html.classList.remove('dark');
        html.classList.add('light');
        if (dot) dot.className = 'w-4 h-4 bg-white rounded-full shadow transition-transform duration-200 translate-x-0';
        if (btn) btn.className = 'w-12 h-6 bg-outline-variant rounded-full p-1 transition-colors duration-200 relative flex items-center';
        localStorage.setItem('theme', 'light');
    } else {
        html.classList.remove('light');
        html.classList.add('dark');
        if (dot) dot.className = 'w-4 h-4 bg-white rounded-full shadow transition-transform duration-200 translate-x-6';
        if (btn) btn.className = 'w-12 h-6 bg-primary rounded-full p-1 transition-colors duration-200 relative flex items-center';
        localStorage.setItem('theme', 'dark');
    }
}

let refreshIntervalId = null;
function updateAutoRefresh(value) {
    if (refreshIntervalId) {
        clearInterval(refreshIntervalId);
        refreshIntervalId = null;
    }
    if (value !== 'none') {
        const ms = parseInt(value);
        refreshIntervalId = setInterval(() => {
            console.log('[Auto-Refresh] Fetching dashboard update...');
            loadDashboardData();
        }, ms);
        alert(`Auto-refresh aktif setiap ${ms / 1000} detik.`);
    } else {
        alert('Auto-refresh dinonaktifkan.');
    }
}

function updateNumberFormat(value) {
    alert(`Format angka diatur ke: ${value === 'id' ? 'Indonesian (Rp 1.000,00)' : 'English'}`);
}

// 7. Client-side Underwriting Filter
function toggleFilterMenu(event) {
    if (event) event.stopPropagation();
    const menu = document.getElementById('filter-menu');
    if (menu) menu.classList.toggle('hidden');
    
    // Close other dropdowns
    document.getElementById('notif-menu').classList.add('hidden');
    document.getElementById('profile-menu').classList.add('hidden');
}

function resetFilters(event) {
    if (event) event.stopPropagation();
    activeFilters = new Set(defaultLiniBisnis);
    const checkboxes = document.querySelectorAll('.filter-checkbox');
    checkboxes.forEach(cb => cb.checked = true);
    applyFilters();
}

function initFilterMenu() {
    const container = document.getElementById('filter-checkboxes-container');
    if (!container) return;
    container.innerHTML = '';
    
    defaultLiniBisnis.forEach(lini => {
        const item = document.createElement('label');
        item.className = 'flex items-center gap-2 cursor-pointer py-1.5 hover:bg-surface-container-low px-2 rounded transition-colors';
        item.innerHTML = `
            <input type="checkbox" checked value="${lini}" class="filter-checkbox rounded text-primary focus:ring-primary border-outline-variant w-4 h-4" onchange="onFilterChange(this)"/>
            <span class="text-on-surface text-xs font-medium">${lini}</span>
        `;
        container.appendChild(item);
    });
}

function onFilterChange(cb) {
    if (cb.checked) {
        activeFilters.add(cb.value);
    } else {
        activeFilters.delete(cb.value);
    }
    applyFilters();
}

function applyFilters() {
    const containers = ['#table-cob-container', '#table-mitra-concentration'];
    containers.forEach(selector => {
        const container = document.querySelector(selector);
        if (!container) return;
        
        const rows = container.querySelectorAll('tbody tr');
        rows.forEach(row => {
            const labelCell = row.querySelector('td');
            if (!labelCell) return;
            const text = labelCell.textContent.trim();
            // Do not hide composite total row
            if (text.includes("TOTAL")) {
                row.style.display = '';
                return;
            }
            
            // Check match with active filter tags
            let matched = false;
            activeFilters.forEach(f => {
                if (text.toLowerCase() === f.toLowerCase() || text.toLowerCase().includes(f.toLowerCase())) {
                    matched = true;
                }
            });
            
            row.style.display = matched ? '' : 'none';
        });
    });
}

// 8. Global Click Listener to close menus on click-outside
document.addEventListener('click', (event) => {
    const notifMenu = document.getElementById('notif-menu');
    const profileMenu = document.getElementById('profile-menu');
    const filterMenu = document.getElementById('filter-menu');
    const supportModal = document.getElementById('support-modal');
    const settingsModal = document.getElementById('settings-modal');
    
    if (notifMenu && !notifMenu.classList.contains('hidden') && !event.target.closest('#notif-menu') && !event.target.closest('button[onclick*="toggleNotificationMenu"]')) {
        notifMenu.classList.add('hidden');
    }
    if (profileMenu && !profileMenu.classList.contains('hidden') && !event.target.closest('#profile-menu') && !event.target.closest('div[onclick*="toggleProfileMenu"]')) {
        profileMenu.classList.add('hidden');
    }
    if (filterMenu && !filterMenu.classList.contains('hidden') && !event.target.closest('#filter-menu') && !event.target.closest('button[onclick*="toggleFilterMenu"]')) {
        filterMenu.classList.add('hidden');
    }
    if (supportModal && !supportModal.classList.contains('hidden') && event.target === supportModal) {
        closeSupportModal();
    }
    if (settingsModal && !settingsModal.classList.contains('hidden') && event.target === settingsModal) {
        closeSettingsModal();
    }
});
