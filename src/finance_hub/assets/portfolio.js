/* Portfolio view controller. The portfolio pipeline is slow and needs LSEG
 * Workspace, so this view never blocks on it: it renders whatever the last
 * pipeline run cached (see portfolio_tracker/cli.py's _write_payload_cache)
 * and offers a Refresh button that reruns the pipeline on a background
 * thread (see finance_hub/portfolio_api.py), polling /api/portfolio/status
 * until it finishes.
 */
window.HubPortfolio = (() => {
  const CAD = new Intl.NumberFormat('en-CA', {style:'currency',currency:'CAD',maximumFractionDigits:0});
  const CAD2 = new Intl.NumberFormat('en-CA', {style:'currency',currency:'CAD',minimumFractionDigits:2,maximumFractionDigits:2});
  const pct = v => (v >= 0 ? '+' : '') + (v * 100).toFixed(2) + '%';
  const pctOrDash = v => Number.isFinite(v) ? pct(v) : '—';
  const PALETTE = ['#5c7cfa','#00e676','#ffd740','#ff6b6b','#a78bfa','#38bdf8','#fb923c','#34d399','#f472b6','#94a3b8'];
  const $ = id => document.getElementById(id);
  function gainClass(v) { return v >= 0 ? 'positive' : 'negative'; }

  let D = null;
  let wired = false;
  let pollTimer = null;
  let returnWindowChart = null, valueChart = null, allocChart = null;
  let sortCol = 'value_cad', sortAsc = false;
  let dailyReturnRows = [];

  // ---- rendering (ported from the standalone portfolio_dashboard.html) ----

  function renderHeader() {
    $('total-value').textContent = CAD.format(D.total_value_cad);
    $('as-of').textContent = 'As of ' + D.as_of_date;
    const gainAmt = D.total_value_cad - D.total_cost_base;
    const gainPct = D.total_cost_base > 0 ? gainAmt / D.total_cost_base : 0;
    const badge = $('gain-badge');
    badge.textContent = CAD.format(gainAmt) + ' ' + pct(gainPct);
    badge.className = 'card-value ' + gainClass(gainAmt);
  }

  function setPerfCard(id, horizonKey, field) {
    const row = (D.performance || []).find(r => r.Horizon === horizonKey);
    const el = $(id);
    const value = row == null ? null : row[field];
    if (Number.isFinite(value)) {
      el.textContent = pct(value);
      el.className = 'card-value ' + gainClass(value);
    } else { el.textContent = 'N/A'; }
  }

  function renderCards() {
    setPerfCard('card-total-return', 'Since-inception', 'Portfolio Return');
    setPerfCard('card-one-year', '1Y', 'Portfolio Return');
    setPerfCard('card-ytd', 'YTD', 'Portfolio Return');
    const sharpeEl = $('card-sharpe');
    const sh = D.risk && D.risk['Sharpe'] != null ? D.risk['Sharpe'] : null;
    if (sh != null) { sharpeEl.textContent = sh.toFixed(2); sharpeEl.className = 'card-value ' + gainClass(sh); }
    $('card-cost-base').textContent = CAD.format(D.total_cost_base);
  }

  function renderBenchmarkMethod() {
    const blendText = ((D.benchmark && D.benchmark.blend) || [])
      .map(item => (item.label || item.ticker) + ' ' + (item.weight * 100).toFixed(1) + '%')
      .join(' · ');
    const comparisonNotes = ((D.benchmark && D.benchmark.comparability_notes) || []).join(' ');
    $('benchmark-method').textContent =
      'Contributions and withdrawals are excluded from both the portfolio and the daily-rebalanced allocation blend.' +
      (blendText ? ' Current blend: ' + blendText + '.' : '') +
      (comparisonNotes ? ' Comparability notes: ' + comparisonNotes : '');
  }

  function renderPerformanceTable() {
    const performanceBody = $('performance-body');
    performanceBody.innerHTML = '';
    for (const row of (D.performance || [])) {
      const difference = row.Difference;
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td>' + row.Horizon + '</td>' +
        '<td>' + row['Return Basis'] + '</td>' +
        '<td class="right ' + gainClass(row['Portfolio TWR']) + '">' + pctOrDash(row['Portfolio TWR']) + '</td>' +
        '<td class="right ' + gainClass(row['Benchmark TWR']) + '">' + pctOrDash(row['Benchmark TWR']) + '</td>' +
        '<td class="right ' + (Number.isFinite(difference) ? gainClass(difference) : '') + '">' + pctOrDash(difference) + '</td>';
      performanceBody.appendChild(tr);
    }
  }

  const riskDefinitions = [
    ['Annualized Volatility', 'Annualized Volatility', 'pct', 'Annualized standard deviation of daily portfolio returns.'],
    ['Sharpe Ratio', 'Sharpe', 'ratio', 'Excess annual return per unit of total volatility.'],
    ['Beta', 'Beta', 'ratio', 'Sensitivity to the blended benchmark; 1.00 moves in line with it.'],
    ['Correlation', 'Correlation', 'ratio', 'Direction and strength of co-movement with the benchmark.'],
    ['Information Ratio', 'Information Ratio', 'ratio', 'Active return per unit of tracking error.'],
    ['Treynor Ratio', 'Treynor', 'pct', 'Annual excess return per unit of benchmark beta.'],
    ["Jensen's Alpha", "Jensen's Alpha", 'pct', 'Annual return above the CAPM-implied return.'],
    ['Probability of Loss', 'Probability of Loss', 'pct', 'Parametric probability of a negative annual return.'],
    ['95% VaR Return Threshold', 'VaR 95% (%)', 'pct', 'Estimated lower-tail annual return at 95% confidence.'],
    ['95% VaR (CAD)', 'VaR 95% ($)', 'cad', 'Current-value equivalent of the 95% return threshold.'],
    ['99% VaR Return Threshold', 'VaR 99% (%)', 'pct', 'Estimated lower-tail annual return at 99% confidence.'],
    ['99% VaR (CAD)', 'VaR 99% ($)', 'cad', 'Current-value equivalent of the 99% return threshold.'],
  ];
  function formatRisk(value, format) {
    if (!Number.isFinite(value)) return 'N/A';
    if (format === 'pct') return pct(value);
    if (format === 'cad') return CAD.format(value);
    return value.toFixed(2);
  }
  function renderRiskGrid() {
    const riskGrid = $('risk-grid');
    riskGrid.innerHTML = '';
    for (const [label, key, format, help] of riskDefinitions) {
      const value = D.risk ? D.risk[key] : null;
      const stat = document.createElement('div');
      stat.className = 'stat';
      stat.innerHTML = '<div class="stat-label">' + label + '</div>' +
        '<div class="stat-value">' + formatRisk(value, format) + '</div>' +
        '<div class="stat-help">' + help + '</div>';
      riskGrid.appendChild(stat);
    }
  }

  function setReturnValue(id, value) {
    const el = $(id);
    if (!Number.isFinite(value)) { el.textContent = 'N/A'; el.className = 'card-value'; return; }
    el.textContent = pct(value);
    el.className = 'card-value ' + gainClass(value);
  }

  function timeWeightedReturn(start, end, field) {
    let factor = 1, observations = 0;
    for (const row of dailyReturnRows) {
      if (row.date <= start || row.date > end || !Number.isFinite(row[field])) continue;
      factor *= 1 + row[field];
      observations += 1;
    }
    return observations ? factor - 1 : NaN;
  }
  function annualizedTwr(periodReturn, start, end) {
    if (!Number.isFinite(periodReturn) || periodReturn < -1) return NaN;
    const days = Math.max(0, (new Date(end + 'T00:00:00Z') - new Date(start + 'T00:00:00Z')) / 86400000);
    if (days === 0) return 0;
    return Math.expm1(Math.log1p(periodReturn) * 365 / days);
  }
  function shiftYears(dateText, years) {
    const d = new Date(dateText + 'T00:00:00Z');
    d.setUTCFullYear(d.getUTCFullYear() - years);
    return d.toISOString().slice(0, 10);
  }

  function renderValueWindow(start, end) {
    const points = (D.daily_values || []).filter(d => d.date >= start && d.date <= end);
    const step = Math.max(1, Math.floor(points.length / 500));
    const plotted = points.filter((_, i) => i % step === 0 || i === points.length - 1);
    const flows = (D.external_flows || []).slice().sort((a, b) => a.date < b.date ? -1 : 1);
    let fi = 0, net = 0;
    const contributed = plotted.map(d => { while (fi < flows.length && flows[fi].date <= d.date) net += flows[fi++].flow_cad; return net; });
    if (valueChart) valueChart.destroy();
    valueChart = new Chart($('value-chart'), {
      type: 'line',
      data: { labels: plotted.map(d => d.date), datasets: [{
        label: 'Portfolio value', data: plotted.map(d => d.value_cad),
        borderColor: '#00e676', backgroundColor: 'rgba(0,230,118,.07)',
        borderWidth: 2, pointRadius: 0, pointHitRadius: 24,
        pointHoverRadius: 7, pointHoverBorderWidth: 2,
        pointHoverBackgroundColor: '#00e676', pointHoverBorderColor: '#fff',
        fill: true, tension: 0.3,
      }, {
        // Drawn transparent (not hidden) so the y-axis doesn't rescale when it appears on hover.
        label: 'Net contributions', data: contributed, borderColor: 'transparent', borderWidth: 1.5, borderDash: [6, 4],
        pointRadius: 0, pointHoverRadius: 7, pointHoverBorderWidth: 2,
        pointHoverBackgroundColor: '#ffb74d', pointHoverBorderColor: '#fff',
        fill: false, stepped: true,
      }]},
      // afterEvent, not options.onHover: Chart.js skips onHover for mouseout, so the line never hid.
      plugins: [{ id: 'contribHover', afterEvent: chart => {
        const line = chart.data.datasets[1], color = chart.getActiveElements().length ? '#ffb74d' : 'transparent';
        if (line.borderColor !== color) { line.borderColor = color; chart.update('none'); }
      } }],
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode:'index', axis:'x', intersect:false },
        plugins: { legend: {display:false}, tooltip: { mode:'index', intersect:false, callbacks: {
          label: ctx => ctx.dataset.label + ': ' + CAD2.format(ctx.parsed.y),
          // Fixed swatches: the contributions line's own colour is transparent until hover.
          labelColor: ctx => { const c = ctx.datasetIndex ? '#ffb74d' : '#00e676'; return { borderColor: c, backgroundColor: c }; },
        } } },
        scales: {
          x: { ticks: { maxTicksLimit: 8, font:{size:11} }, grid: { color: '#2e3248' } },
          y: { ticks: { callback: v => CAD.format(v), font:{size:11} }, grid: { color: '#2e3248' } },
        }
      }
    });
  }

  function renderReturnWindow() {
    const start = $('window-start').value, end = $('window-end').value;
    const error = $('pf-window-error');
    if (!start || !end || start > end) { error.textContent = 'Choose a valid start date on or before the end date.'; return; }
    const rows = dailyReturnRows.filter(r => r.date > start && r.date <= end);
    if (!rows.length) { error.textContent = 'No return data is available for this window.'; return; }
    error.textContent = '';
    const portfolioPeriod = timeWeightedReturn(start, end, 'portfolio');
    const benchmarkPeriod = timeWeightedReturn(start, end, 'benchmark');
    const portfolioAnnualized = annualizedTwr(portfolioPeriod, start, end);
    const benchmarkAnnualized = annualizedTwr(benchmarkPeriod, start, end);
    setReturnValue('window-portfolio', portfolioAnnualized);
    setReturnValue('window-benchmark', benchmarkAnnualized);
    setReturnValue('window-difference', portfolioAnnualized - benchmarkAnnualized);
    setReturnValue('window-period', portfolioPeriod);
    renderValueWindow(start, end);

    const step = Math.max(1, Math.floor(rows.length / 200));
    let portfolioFactor = 1, benchmarkFactor = 1;
    const plotted = [{date:start, portfolio:0, benchmark:0}];
    rows.forEach((row, i) => {
      if (Number.isFinite(row.portfolio)) portfolioFactor *= 1 + row.portfolio;
      if (Number.isFinite(row.benchmark)) benchmarkFactor *= 1 + row.benchmark;
      if (i % step === 0 || i === rows.length - 1) {
        plotted.push({ date: row.date, portfolio: portfolioFactor - 1, benchmark: benchmarkFactor - 1 });
      }
    });
    if (returnWindowChart) returnWindowChart.destroy();
    returnWindowChart = new Chart($('return-window-chart'), {
      type: 'line',
      data: { labels: plotted.map(p => p.date), datasets: [
        {label:'Portfolio cumulative return', data:plotted.map(p => Number.isFinite(p.portfolio) ? p.portfolio * 100 : null), borderColor:'#00e676', borderWidth:2, pointRadius:0, pointHitRadius:24, pointHoverRadius:5, tension:.2},
        {label:'Benchmark cumulative return', data:plotted.map(p => Number.isFinite(p.benchmark) ? p.benchmark * 100 : null), borderColor:'#5c7cfa', borderWidth:2, pointRadius:0, pointHitRadius:24, pointHoverRadius:5, tension:.2},
      ]},
      options: {
        responsive:true, maintainAspectRatio:false,
        interaction:{mode:'index',axis:'x',intersect:false},
        plugins:{legend:{labels:{color:'#e2e6f3'}},tooltip:{mode:'index',intersect:false,callbacks:{label:ctx => ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(2) + '%'}}},
        scales:{
          x:{ticks:{maxTicksLimit:8,font:{size:11}},grid:{color:'#2e3248'}},
          y:{ticks:{callback:v => v.toFixed(0) + '%',font:{size:11}},grid:{color:'#2e3248'}}
        }
      }
    });
  }

  function setupReturnWindow() {
    dailyReturnRows = (D.daily_returns || []).filter(r => r.date).sort((a,b) => a.date.localeCompare(b.date));
    if (!dailyReturnRows.length) return;
    const minDate = dailyReturnRows[0].date, maxDate = dailyReturnRows[dailyReturnRows.length - 1].date;
    const startInput = $('window-start'), endInput = $('window-end');
    startInput.min = minDate; startInput.max = maxDate; startInput.value = minDate;
    endInput.min = minDate; endInput.max = maxDate; endInput.value = maxDate;
    renderReturnWindow();
  }

  const EXCLUDED_ALLOCATION_LABELS = new Set(['unknown', 'other']);
  function isExcludedAllocationBucket(name) {
    return EXCLUDED_ALLOCATION_LABELS.has(name) || name.startsWith('not classified');
  }
  function normalizedAllocationItems(key) {
    const classified = ((D.exposure || {})[key] || []).filter(item => {
      const name = String(item.bucket || '').trim().toLowerCase();
      return !isExcludedAllocationBucket(name) && Number(item.weight || 0) > 0;
    });
    const total = classified.reduce((sum, item) => sum + Number(item.weight || 0), 0);
    if (total <= 0) return [];
    // Total is computed across every classified bucket before truncating to the
    // top 10 shown, so displayed shares aren't inflated by buckets left off the chart.
    return classified
      .map(item => ({...item, normalizedWeight: Number(item.weight || 0) / total}))
      .slice(0, 10);
  }
  function renderAlloc(key) {
    const items = normalizedAllocationItems(key);
    if (allocChart) allocChart.destroy();
    allocChart = new Chart($('alloc-chart'), {
      type: 'doughnut',
      data: { labels: items.map(d => d.bucket), datasets: [{ data: items.map(d => +(d.normalizedWeight * 100).toFixed(4)), backgroundColor: PALETTE, borderWidth: 0 }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { position:'right', labels:{ color:'#e2e6f3', boxWidth:12, padding:6, font:{size:11} } },
          tooltip: { callbacks: { label: ctx => ctx.label + ': ' + ctx.parsed.toFixed(1) + '%' } }
        }
      }
    });
  }

  function renderAccountChart() {
    if (D.account_breakdown && D.account_breakdown.length) {
      new Chart($('account-chart'), {
        type: 'bar',
        data: { labels: D.account_breakdown.map(d => d.account_type), datasets: [{ data: D.account_breakdown.map(d => d.value_cad), backgroundColor: PALETTE, borderRadius: 6 }] },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend:{display:false}, tooltip:{ callbacks:{ label: ctx => CAD.format(ctx.parsed.y) } } },
          scales: {
            x: { ticks:{ font:{size:12} }, grid:{display:false} },
            y: { ticks:{ callback: v => CAD.format(v), font:{size:11} }, grid:{ color:'#2e3248' } }
          }
        }
      });
    }
  }

  function renderTable() {
    const accountFilter = $('account-filter');
    const selectedAccount = accountFilter.value;
    const sourceRows = selectedAccount
      ? (D.account_positions || []).filter(p => p.account_type === selectedAccount)
      : (D.positions || []);
    const rows = [...sourceRows].sort((a, b) => {
      const va = a[sortCol] ?? '', vb = b[sortCol] ?? '';
      if (typeof va === 'string') return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
      return sortAsc ? va - vb : vb - va;
    });
    const displayedValue = rows.reduce((total, p) => total + p.value_cad, 0);
    const selectedAccountTotal = selectedAccount
      ? ((D.account_breakdown || []).find(a => a.account_type === selectedAccount) || {}).value_cad
      : D.total_value_cad;
    const weightBase = selectedAccountTotal || displayedValue;
    $('holdings-summary').textContent = rows.length + (rows.length === 1 ? ' holding · ' : ' holdings · ') + CAD.format(displayedValue);
    const tbody = $('holdings-body');
    tbody.innerHTML = '';
    for (const p of rows) {
      const gc = gainClass(p.accrued_gain_cad);
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td><span class="ticker-badge">' + p.ticker + '</span></td>' +
        '<td><span class="name-cell" title="' + p.name + '">' + p.name + '</span></td>' +
        '<td class="right">' + p.units.toFixed(4) + '</td>' +
        '<td class="right">' + CAD2.format(p.price_cad) + '</td>' +
        '<td class="right">' + CAD.format(p.value_cad) + '</td>' +
        '<td class="right">' + CAD.format(p.total_acb) + '</td>' +
        '<td class="right ' + gc + '">' + CAD.format(p.accrued_gain_cad) + '</td>' +
        '<td class="right ' + gc + '">' + pct(p.accrued_gain_pct) + '</td>' +
        '<td class="right">' + (weightBase ? p.value_cad / weightBase * 100 : 0).toFixed(1) + '%</td>';
      tbody.appendChild(tr);
    }
  }

  function renderHoldings() {
    const accountFilter = $('account-filter');
    const selected = accountFilter.value;
    accountFilter.innerHTML = '<option value="">All accounts</option>';
    const accountTypes = [...new Set([
      ...(D.account_positions || []).map(p => p.account_type),
      ...(D.account_breakdown || []).map(p => p.account_type),
    ])].sort();
    for (const accountType of accountTypes) {
      const option = document.createElement('option');
      option.value = accountType; option.textContent = accountType;
      accountFilter.appendChild(option);
    }
    accountFilter.value = accountTypes.includes(selected) ? selected : '';
    renderTable();
  }

  function renderAll() {
    renderHeader();
    renderCards();
    renderBenchmarkMethod();
    renderPerformanceTable();
    renderRiskGrid();
    setupReturnWindow();
    renderAlloc(document.querySelector('#exp-tabs .tab.active')?.dataset.key || 'asset_class');
    renderAccountChart();
    renderHoldings();
  }

  // ---- one-time event wiring ----
  function wire() {
    $('window-start').addEventListener('change', renderReturnWindow);
    $('window-end').addEventListener('change', renderReturnWindow);
    document.querySelectorAll('#view-portfolio [data-range]').forEach(button => button.addEventListener('click', () => {
      const range = button.dataset.range;
      const minDate = $('window-start').min, maxDate = $('window-start').max;
      let nextStart = minDate;
      if (range === 'ytd') nextStart = maxDate.slice(0, 4) + '-01-01';
      if (range === '1y') nextStart = shiftYears(maxDate, 1);
      if (range === '3y') nextStart = shiftYears(maxDate, 3);
      if (range === '5y') nextStart = shiftYears(maxDate, 5);
      $('window-start').value = nextStart < minDate ? minDate : nextStart;
      $('window-end').value = maxDate;
      renderReturnWindow();
    }));
    $('exp-tabs').addEventListener('click', e => {
      const tab = e.target.closest('[data-key]');
      if (!tab) return;
      document.querySelectorAll('#exp-tabs .tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      renderAlloc(tab.dataset.key);
    });
    $('account-filter').addEventListener('change', renderTable);
    document.querySelector('#holdings-table thead').addEventListener('click', e => {
      const th = e.target.closest('[data-col]');
      if (!th) return;
      const col = th.dataset.col;
      if (sortCol === col) sortAsc = !sortAsc; else { sortCol = col; sortAsc = false; }
      renderTable();
    });
    $('pf-refresh').addEventListener('click', startRefresh);
  }

  // ---- refresh action ----
  async function startRefresh() {
    $('pf-refresh').disabled = true;
    $('pf-refresh-status').textContent = 'Starting…';
    try {
      const res = await fetch('/api/portfolio/refresh', {method:'POST'});
      const data = await res.json();
      if (!res.ok) {
        $('pf-refresh-status').textContent = data.error || 'Refresh failed to start.';
        $('pf-refresh').disabled = false;
        return;
      }
    } catch (e) {
      $('pf-refresh-status').textContent = 'Refresh failed to start: ' + e.message;
      $('pf-refresh').disabled = false;
      return;
    }
    pollStatus();
  }

  function pollStatus() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(async () => {
      try {
        const status = await (await fetch('/api/portfolio/status')).json();
        $('pf-refresh-log').style.display = status.log && status.log.length ? '' : 'none';
        $('pf-refresh-log').textContent = (status.log || []).join('\n');
        if (status.state === 'running') {
          $('pf-refresh-status').textContent = 'Running…';
          pollStatus();
        } else {
          $('pf-refresh').disabled = false;
          $('pf-refresh-status').textContent = status.state === 'error'
            ? 'Failed: ' + (status.error || 'see log below')
            : 'Refreshed.';
          if (status.state !== 'error') load();
        }
      } catch (e) {
        $('pf-refresh').disabled = false;
        $('pf-refresh-status').textContent = 'Lost contact with the server: ' + e.message;
      }
    }, 1500);
  }

  // ---- entry point ----
  async function load() {
    const res = await fetch('/api/portfolio/payload');
    const data = await res.json();
    if (!wired) { wire(); wired = true; }
    if (!data.payload) {
      $('pf-empty').style.display = '';
      $('pf-content').style.display = 'none';
      return;
    }
    D = data.payload;
    $('pf-empty').style.display = 'none';
    $('pf-content').style.display = '';
    renderAll();
    const staleness = data.stale_days;
    $('pf-refresh-status').textContent = data.generated_at
      ? `Last refreshed ${new Date(data.generated_at).toLocaleString()}` + (staleness != null && staleness >= 1 ? ` (${staleness.toFixed(1)} days ago)` : '')
      : '';
  }

  async function init() {
    // Pick up a refresh that's already running from a previous tab visit.
    try {
      const status = await (await fetch('/api/portfolio/status')).json();
      if (status.state === 'running') {
        if (!wired) { wire(); wired = true; }
        $('pf-refresh').disabled = true;
        pollStatus();
      }
    } catch (e) { /* server not reachable yet; load() below will surface it */ }
    await load();
  }

  return {init};
})();
