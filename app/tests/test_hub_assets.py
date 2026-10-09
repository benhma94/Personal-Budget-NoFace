"""Tests for the finance-hub static asset bundle: the shell HTML plus the
per-view CSS/JS that used to live in three separate Python HTML-template
modules — budget_dashboard/html.py, portfolio_tracker/io_html.py, and
journal_entry/html.py — all deleted now that the hub serves one page with
four tabs instead of three standalone dashboards.
"""
from __future__ import annotations

import re
from pathlib import Path

_ASSETS = Path(__file__).parent.parent / "src" / "finance_hub" / "assets"
_VIEW_PREFIXES = {
    "budget": ".view-budget",
    "budget-plan": ".view-budget-plan",
    "portfolio": ".view-portfolio",
    "retirement": ".view-retirement",
    "journal": ".view-journal",
}


def _read(name: str) -> str:
    return (_ASSETS / name).read_text(encoding="utf-8")


def test_every_declared_asset_file_exists():
    for name in (
        "shell.html", "theme.css", "hub.js",
        "budget.css", "budget.js",
        "budget-plan.css", "budget-plan.js",
        "portfolio.css", "portfolio.js",
        "retirement.css", "retirement.js",
        "journal.css", "journal.js",
    ):
        assert (_ASSETS / name).is_file(), name


def test_shell_wires_up_the_five_top_level_views():
    shell = _read("shell.html")
    for view in ("budget", "budget-plan", "portfolio", "retirement", "journal"):
        assert f'data-view="{view}"' in shell
        assert f'id="view-{view}"' in shell


def test_top_level_nav_uses_the_expected_order():
    shell = _read("shell.html")
    nav = re.search(r'<nav class="hub-nav"[^>]*>(.*?)</nav>', shell, re.DOTALL).group(1)
    order = re.findall(r'data-view="([\w-]+)"', nav)
    assert order == ["budget", "budget-plan", "portfolio", "retirement", "journal"]


def test_budget_plan_view_has_year_switch_and_action_buttons():
    shell = _read("shell.html")
    for element_id in (
        "bp-prev-year", "bp-next-year", "bp-year-label",
        "bp-fill-suggestions", "bp-save", "bp-unsaved", "bp-status", "bp-table",
    ):
        assert f'id="{element_id}"' in shell


def test_shell_wires_up_all_six_journal_tabs():
    shell = _read("shell.html")
    for tab in ("import", "review", "transfers", "close", "post", "rules"):
        assert f'data-tab="{tab}"' in shell


def test_no_duplicate_element_ids_in_the_shell():
    """Regression guard: the standalone budget and portfolio dashboards both
    used id="window-error" before consolidation; portfolio's was renamed to
    pf-window-error so the two views can share one document."""
    shell = _read("shell.html")
    ids = re.findall(r'id="([a-zA-Z0-9_-]+)"', shell)
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    assert not duplicates


def test_css_files_scope_every_selector_under_their_view_namespace():
    """The three original dashboards defined 17 class names in common (.card,
    .panel, .tab, .stat, ...), so every selector in a per-view stylesheet
    must be scoped under that view's namespace or they'd collide now that
    all three share one page. Only theme.css may define bare/global rules."""
    for view, prefix in _VIEW_PREFIXES.items():
        css = _read(f"{view}.css")
        for line in css.splitlines():
            line = line.strip()
            if not line or line.startswith("/*") or line.startswith("@media") or line == "}":
                continue
            if "{" not in line:
                continue
            selector_part = line.split("{", 1)[0]
            for selector in selector_part.split(","):
                selector = selector.strip()
                if selector:
                    assert selector.startswith(prefix), f"{view}.css: unscoped selector {selector!r}"


def test_budget_js_calls_the_payload_api_not_a_global():
    js = _read("budget.js")
    assert "/api/budget/payload" in js
    assert "window.BUDGET_DATA" not in js


def test_budget_cash_flow_renders_surplus_as_bars():
    js = _read("budget.js")
    assert "{type:'bar',label:'Surplus'" in js
    assert "{label:'Income'" in js
    assert "{label:'Spending'" in js


def test_budget_balance_sheet_displays_cents_without_changing_other_money_formats():
    js = _read("budget.js")
    assert "const BALANCE_CAD = new Intl.NumberFormat('en-CA', {style:'currency',currency:'CAD',minimumFractionDigits:2,maximumFractionDigits:2});" in js
    assert "$('asset-total').textContent=BALANCE_CAD.format" in js
    assert "$('liability-total').textContent=BALANCE_CAD.format" in js
    assert "<strong>${BALANCE_CAD.format(item.balance||0)}</strong>" in js
    assert "const money = v => CAD.format(v||0);" in js


def test_budget_category_detail_headings_are_sortable():
    shell = _read("shell.html")
    js = _read("budget.js")
    for key in ("category", "actual", "compare", "delta"):
        assert f'data-detail-sort="{key}"' in shell
    assert "categorySortDirection" in js
    assert "setAttribute('aria-sort'" in js


def test_budget_presents_investment_results_separately_from_operating_income():
    shell = _read("shell.html")
    js = _read("budget.js")
    for element_id in ("income", "investment-income", "income-combined"):
        assert f'id="{element_id}"' in shell
    assert "Operating income" in shell
    assert "Operating surplus / deficit" in shell
    assert "Investment gain / loss" in shell
    assert "investmentContext" in js
    assert "row.kind==='investment'" in js
    assert "Investment gain / loss is unbudgeted" in js


def test_budget_progress_colour_follows_favourable_variance_sign():
    js = _read("budget.js")
    css = _read("budget.css")
    assert "delta>0?'positive':delta<0?'negative'" in js
    assert ".view-budget .progress span.positive" in css
    assert ".view-budget .progress span.negative" in css


def test_budget_actual_legend_has_an_explicit_normal_income_colour():
    js = _read("budget.js")
    assert "const item=sortedRows[context.dataIndex]" in js
    assert ":'#6c8cff'" in js
    assert "generateLabels:categoryLegendLabels" in js
    assert "item.datasetIndex===0" in js
    assert "fillStyle:'#6c8cff',strokeStyle:'#6c8cff'" in js


def test_spending_donut_uses_every_positive_category_with_stable_colours():
    js = _read("budget.js")
    css = _read("budget.css")
    shell = _read("shell.html")

    assert "row.kind==='expense'&&row.actual>0" in js
    assert "slice(0,8)" not in js
    assert "spendingColor(row.category)" in js
    assert "context.parsed/total*100" in js
    assert 'id="spending-legend"' in shell
    assert ".view-budget .spending-legend" in css
    assert "overflow-y: auto" in css


def test_budget_transaction_drawer_is_read_only_and_period_aware():
    js = _read("budget.js")
    shell = _read("shell.html")

    assert "/api/budget/transactions" in js
    assert "TX_PAGE_SIZE = 100" in js
    assert "comparisonKeys" in js
    assert "method:'POST'" not in js
    for element_id in (
        "tx-backdrop", "tx-start", "tx-end", "tx-category", "tx-account",
        "tx-search", "tx-body", "tx-prev", "tx-next",
    ):
        assert f'id="{element_id}"' in shell


def test_budget_savings_rate_names_its_operating_income_basis():
    shell = _read("shell.html")
    assert '<div class="rate-basis">of operating income</div>' in shell


def test_portfolio_js_retains_return_window_behaviour():
    js = _read("portfolio.js")
    assert "/api/portfolio/payload" in js
    assert "timeWeightedReturn" in js
    assert "annualizedTwr" in js
    assert "moneyWeightedReturn" not in js


def test_portfolio_value_tooltip_shows_net_contributions():
    js = _read("portfolio.js")
    assert "D.external_flows" in js
    assert "labelColor: ctx =>" in js
    assert "pointHoverBackgroundColor: '#ffb74d'" in js
    # Contributions line is drawn only while hovering the chart.
    assert "label: 'Net contributions', data: contributed, borderColor: 'transparent'" in js
    assert "afterEvent: chart =>" in js


def test_portfolio_js_wires_refresh_and_status_polling():
    js = _read("portfolio.js")
    assert "/api/portfolio/refresh" in js
    assert "/api/portfolio/status" in js


def test_portfolio_uses_the_renamed_window_error_id():
    assert 'id="pf-window-error"' in _read("shell.html")
    assert "pf-window-error" in _read("portfolio.js")


def test_portfolio_value_and_gain_are_headline_tiles():
    shell = _read("shell.html")
    cards = re.search(r'<div class="cards">(.*?)</div>\s*\n\s*<div class="section">', shell, re.DOTALL)
    assert cards is not None
    assert 'class="card-label">Total Portfolio Value' in cards.group(1)
    assert 'class="card-label">Accrued Gain' in cards.group(1)
    assert 'class="header"' not in shell


def test_retirement_js_wires_defaults_forecast_and_local_persistence():
    js = _read("retirement.js")
    assert "/api/retirement/defaults" in js
    assert "/api/retirement/forecast" in js
    assert "financeHub.retirement.v3" in js
    assert "financeHub.retirement.v2" in js
    assert "if (!data.probabilistic)" in js
    assert "restart NOFACE" in js
    assert "localStorage" in js
    assert "MONEY_INPUT" in js
    assert "replace(/,/g, '')" in js
    apply_benefits = re.search(
        r"function applyCppOasEstimate\(estimate\) \{(.*?)\n  \}", js, re.DOTALL
    )
    assert apply_benefits is not None
    assert "removeDuplicatedCppOasBaseIncome();" in apply_benefits.group(1)
    assert "$('rt-income').value = MONEY_INPUT.format(estimate.annual)" not in js
    shell = _read("shell.html")
    for element_id in ("rt-portfolio", "rt-spending", "rt-income", "rt-contribution"):
        assert re.search(rf'id="{element_id}"[^>]+data-money', shell)
    assert re.search(r'id="rt-start-date"[^>]+type="date"', shell)
    assert re.search(r'id="rt-birth-date"[^>]+type="date"', shell)
    assert 'id="rt-plan-age"' in shell
    assert 'id="rt-volatility"' in shell
    assert 'id="rt-success-probability"' in shell
    assert 'id="rt-income-streams"' in shell
    assert 'id="rt-spending-phases"' in shell
    assert 'id="rt-one-time-events"' in shell
    assert "CPP/OAS uses published average/maximum benefit amounts" in shell
    for element_id in (
        "rt-tax-exempt-card", "rt-tax-exempt-total", "rt-tax-exempt-share",
    ):
        assert f'id="{element_id}"' in shell
    assert "Forecast basis" not in shell


def test_retirement_shows_cpp_oas_calculation_and_retirement_chart_marker():
    shell = _read("shell.html")
    js = _read("retirement.js")
    css = _read("retirement.css")

    for element_id in (
        "rt-benefit-title",
        "rt-cpp-basis",
        "rt-cpp-adjustment",
        "rt-cpp-start",
        "rt-cpp-monthly",
        "rt-oas-basis",
        "rt-oas-adjustment",
        "rt-oas-start",
        "rt-oas-monthly",
        "rt-benefit-annual",
    ):
        assert f'id="{element_id}"' in shell

    for element_id in ("rt-cpp-basis", "rt-oas-basis"):
        assert re.search(rf'id="{element_id}"[^>]+data-benefit-field', shell)
    for element_id in ("rt-cpp-start", "rt-oas-start"):
        assert re.search(rf'id="{element_id}"[^>]+type="date"[^>]+data-benefit-field', shell)
    for element_id in (
        "rt-retirement-age-note", "rt-cpp-start-age", "rt-oas-start-age",
    ):
        assert f'id="{element_id}"' in shell
    assert "retirementMarkerPlugin" in js
    assert "retirementMarker:{xValue:retirementOffset" in js
    assert "A red dotted line marks retirement" in js
    assert "kind:'cpp_oas_estimate'" in js
    assert "syncCppOasForecast" in js
    assert "Payout age" in js
    assert "Retirement age" in js
    assert "input.addEventListener('change'" in js
    assert "$('rt-cpp-start').min" not in js
    assert "$('rt-oas-start').min" not in js
    assert "dated income streams update automatically" in js
    assert ".view-retirement .benefit-calculator" in css


def test_journal_js_no_longer_offers_to_regenerate_a_static_dashboard():
    js = _read("journal.js")
    assert "/api/dashboard/regenerate" not in js
    assert "regenerate" not in js.lower()


def test_journal_js_calls_expected_api_endpoints():
    js = _read("journal.js")
    for endpoint in (
        "/api/accounts", "/api/transactions", "/api/rules", "/api/transfers/suggested",
        "/api/import/sniff", "/api/import/mapping", "/api/import/commit",
        "/api/transactions/categorize", "/api/transactions/status",
        "/api/transfers/confirm", "/api/transfers/unlink",
        "/api/month-close", "/api/month-close/preview", "/api/month-close/stage",
        "/api/post/preview", "/api/post", "/api/batches",
    ):
        assert endpoint in js, endpoint


def test_hub_sends_a_heartbeat_so_the_server_can_detect_the_tab_closing():
    js = _read("hub.js")
    assert "/api/heartbeat" in js
    assert "method: 'POST'" in js
    assert "setInterval" in js


def test_hub_initializes_budget_plan_and_guards_hash_navigation():
    js = _read("hub.js")
    assert "'budget-plan'" in js
    assert "window.HubBudgetPlan.init()" in js
    assert "hasUnsavedChanges" in js
    assert "isSaving" in js
    assert "discardChanges" in js
    assert "confirm(" in js


def test_budget_plan_controller_covers_races_invalid_input_and_safe_fill():
    js = _read("budget-plan.js")
    assert "/api/budget-plan/payload" in js
    assert "/api/budget-plan/save" in js
    assert "window.HubBudgetPlan" in js
    assert "requestSequence" in js
    assert "saveSequence" in js
    assert "Number.isFinite" in js
    assert "classes.push('invalid')" in js
    assert "cell.saved == null && cell.suggested != null" in js
    assert "hasStagedEdit" in js
    assert "original !== value" in js
    assert "method: 'POST'" in js
    assert "function toast(" in js


def test_status_bar_only_clears_once_no_cell_is_left_invalid():
    """setInvalidInput used to reset the status bar by comparing its text to
    the literal 'Enter a valid number.' string, so fixing one invalid cell
    could mask that a different cell was still invalid. It must instead
    check whether any cell anywhere in the grid is still invalid."""
    js = _read("budget-plan.js")
    assert "function anyInvalid(" in js
    assert "if (!anyInvalid())" in js
    assert "$('bp-status').textContent === 'Enter a valid number.'" not in js


def test_change_year_rejects_requests_outside_the_servers_accepted_range():
    js = _read("budget-plan.js")
    assert "requested < 1900 || requested > 9999" in js


def test_year_label_resyncs_to_the_actual_year_after_a_failed_load():
    """changeYear used to commit to the new year immediately, and a failed
    load never updated bp-year-label, so a failed prev/next-year click left
    the label showing a year that no longer matched internal state."""
    js = _read("budget-plan.js")
    assert "if (plan) year = plan.year;" in js


def test_journal_review_keeps_its_scroll_position_across_a_row_edit():
    js = _read("journal.js")
    assert "captureViewPosition" in js
    assert "restoreViewPosition" in js
    assert "scrollTop" in js
    assert "preventScroll:true" in js  # focus must not fight the restore
    assert 'id="rv-table-wrap"' in js
    assert "repaintReviewTable" in js


def test_shell_header_shows_noface_brand():
    shell = _read("shell.html")
    assert "NOFACE" in shell
    assert "Net-worth, Outflows, Financial Accounts, Cashflow &amp; Expenses" in shell
    assert "brand-mark" in shell
