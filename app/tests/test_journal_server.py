"""End-to-end smoke test: a real HTTP server on an ephemeral port, exercised
with real HTTP requests, to prove the routing/JSON/error-status wiring in
finance_hub/server.py actually works — app.py's own tests never touch this
layer. Originally tested journal_entry/server.py in isolation; the hub now
serves the Budget and Portfolio routes from the same process, so this file
covers those too.
"""
from __future__ import annotations

from datetime import date
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from finance_hub.budget_api import BudgetPayloadCache
from finance_hub.idle_monitor import IdleMonitor
from finance_hub.portfolio_api import PortfolioRefreshJob
from finance_hub.server import make_handler
from journal_entry.app import JournalApp

from .journal_fixture import build_fixture_workbook
from .budget_plan_fixture import build_budget_plan_fixture

_ACCOUNTS = ["Cash", "Rewards Card", "Food", "Salary"]
_CLOSE_ACCOUNTS = [
    "Cash", "Investment Account", "Transit Card", "Investment Income", "Transit"
]


@pytest.fixture
def running_budget_plan_server(tmp_path):
    from journal_entry.xlsx_append import JournalLine, append_journal_rows

    workbook_path = tmp_path / "Personal Budget.xlsx"
    build_budget_plan_fixture(
        workbook_path, month_end_dates=[date(2026, 1, 31)],
        category_rows={16: "Salary", 28: "Food"}, values={(16, 0): 900},
    )
    append_journal_rows(workbook_path, [
        JournalLine(date(2026, 1, 5), "Primary Checking", "Salary", [950], "Pay"),
    ])
    app = JournalApp.create(workbook_path, tmp_path / "journal.sqlite")
    cache = BudgetPayloadCache(workbook_path)
    payload = tmp_path / "portfolio.json"
    handler = make_handler(
        app, cache, PortfolioRefreshJob(tmp_path / "portfolio.xlsx", payload),
        payload, IdleMonitor(timeout_seconds=60), workbook_path,
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", workbook_path, cache
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()
        app.close()


@pytest.mark.parametrize("query", ["", "?year=nope", "?year=0", "?year=10000"])
def test_budget_plan_payload_rejects_invalid_year(running_server, query):
    base_url, _ = running_server
    status, data = _request(base_url, "GET", "/api/budget-plan/payload" + query)
    assert status == 400
    assert data["error"]


def test_budget_plan_payload_requires_budget_sheet(running_server):
    base_url, _ = running_server
    status, data = _request(base_url, "GET", "/api/budget-plan/payload?year=2026")
    assert status == 400
    assert "Budget" in data["error"]


def test_budget_plan_save_refreshes_report_and_appends_months(running_budget_plan_server):
    base_url, _, cache = running_budget_plan_server
    status, before = _request(base_url, "GET", "/api/budget/payload")
    assert status == 200
    assert before["monthly"]["2026-01"]["income_budget"] == 900
    cached_before = cache._payload
    status, plan = _request(base_url, "GET", "/api/budget-plan/payload?year=2026")
    assert status == 200
    assert plan["cells"]["Salary"]["2026-01"]["saved"] == 900
    status, result = _request(base_url, "POST", "/api/budget-plan/save", {"updates": [
        {"category": "Salary", "month": "2026-01", "value": 975.25},
        {"category": "Food", "month": "2027-01", "value": -280.5},
    ]})
    assert (status, result) == (200, {"ok": True})
    assert cache._mtime is None
    status, after = _request(base_url, "GET", "/api/budget/payload")
    assert status == 200
    assert cache._payload is not cached_before
    assert after["monthly"]["2026-01"]["income_budget"] == 975.25
    assert after["monthly"]["2027-01"]["spending_budget"] == 280.5
    status, plan = _request(base_url, "GET", "/api/budget-plan/payload?year=2027")
    assert status == 200
    assert plan["cells"]["Food"]["2027-01"]["saved"] == -280.5


@pytest.mark.parametrize("body", [
    [], {}, {"updates": None}, {"updates": {}}, {"updates": [1]},
    {"updates": [{"category": "Salary"}]},
    {"updates": [{"category": "Salary", "month": "2026-13", "value": 1}]},
    {"updates": [{"category": "Salary", "month": "2026-01", "value": True}]},
])
def test_budget_plan_save_rejects_invalid_body_without_writing(running_budget_plan_server, body):
    base_url, path, _ = running_budget_plan_server
    before = path.read_bytes()
    status, data = _request(base_url, "POST", "/api/budget-plan/save", body)
    assert status == 400
    assert data["error"]
    assert path.read_bytes() == before


def test_budget_plan_save_locked_workbook_keeps_cache(running_budget_plan_server):
    base_url, path, cache = running_budget_plan_server
    before = path.read_bytes()
    cached = cache.get()
    path.with_name("~$" + path.name).write_text("locked")
    status, data = _request(base_url, "POST", "/api/budget-plan/save", {"updates": [
        {"category": "Salary", "month": "2026-01", "value": 1000},
    ]})
    assert status == 409
    assert data["error"]
    assert path.read_bytes() == before
    assert cache._payload is cached


def test_budget_plan_save_verification_error_is_500(running_budget_plan_server, monkeypatch):
    from finance_hub import server
    from journal_entry.xlsx_append import VerificationError

    base_url, _, cache = running_budget_plan_server
    cached = cache.get()

    def fail(*args):
        raise VerificationError("Verification failed; backup restored")

    monkeypatch.setattr(server, "write_budget_plan", fail)
    status, data = _request(base_url, "POST", "/api/budget-plan/save", {"updates": [
        {"category": "Salary", "month": "2026-01", "value": 1000},
    ]})
    assert status == 500
    assert "Verification failed" in data["error"]
    assert cache._payload is cached


def test_budget_plan_save_malformed_json_is_400(running_server):
    base_url, _ = running_server
    req = urllib.request.Request(base_url + "/api/budget-plan/save", data=b"{", method="POST")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(req)
    assert error.value.code == 400
    assert json.loads(error.value.read())["error"]


def test_budget_plan_payload_uses_cache_and_reflects_saves_without_staleness(tmp_path, monkeypatch):
    """read_budget_plan used to re-parse the whole Journal on every payload
    GET. Wiring a BudgetPlanSourceCache into the route must both skip that
    reparse on a repeat GET and never serve a stale value after a save."""
    import finance_hub.budget_plan_api as bp_api
    from finance_hub.budget_plan_api import BudgetPlanSourceCache

    workbook_path = tmp_path / "Personal Budget.xlsx"
    build_budget_plan_fixture(
        workbook_path, month_end_dates=[date(2026, 1, 31)],
        category_rows={16: "Salary", 28: "Food"}, values={(16, 0): 900},
    )
    app = JournalApp.create(workbook_path, tmp_path / "journal.sqlite")
    plan_cache = BudgetPlanSourceCache(workbook_path)

    calls = []
    real_read_journal = bp_api._read_journal

    def spy(*args, **kwargs):
        calls.append(1)
        return real_read_journal(*args, **kwargs)

    monkeypatch.setattr(bp_api, "_read_journal", spy)

    handler = make_handler(
        app, BudgetPayloadCache(workbook_path), object(),
        tmp_path / "portfolio.json", IdleMonitor(timeout_seconds=60), workbook_path,
        budget_plan_cache=plan_cache,
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        status, before = _request(base_url, "GET", "/api/budget-plan/payload?year=2026")
        assert status == 200
        assert before["cells"]["Salary"]["2026-01"]["saved"] == 900

        status, _ = _request(base_url, "GET", "/api/budget-plan/payload?year=2026")
        assert status == 200
        assert len(calls) == 1  # second GET reused the cached parse

        status, result = _request(base_url, "POST", "/api/budget-plan/save", {"updates": [
            {"category": "Salary", "month": "2026-01", "value": 975.25},
        ]})
        assert (status, result) == (200, {"ok": True})

        status, after = _request(base_url, "GET", "/api/budget-plan/payload?year=2026")
        assert status == 200
        assert after["cells"]["Salary"]["2026-01"]["saved"] == 975.25
        assert len(calls) == 2  # save invalidated the cache
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()
        app.close()


def test_budget_plan_cache_is_invalidated_by_journal_writes_too(tmp_path, monkeypatch):
    """Journal writes (post / row edits) change actuals the plan cache
    holds, so they must invalidate it exactly like they already do for
    BudgetPayloadCache."""
    from finance_hub import server
    from finance_hub.budget_plan_api import BudgetPlanSourceCache

    class MutationApp:
        def post(self, posting_date):
            return {"posting_date": posting_date.isoformat()}

        def update_journal_row(self, row_number, **changes):
            return {"row_number": row_number, "amount": changes["amount"]}

    monkeypatch.setattr(server, "write_budget_plan", lambda *args, **kwargs: None)

    plan_cache = BudgetPlanSourceCache(tmp_path / "Personal Budget.xlsx")
    invalidations = []
    monkeypatch.setattr(plan_cache, "invalidate", lambda: invalidations.append(1))

    handler = make_handler(
        MutationApp(),
        BudgetPayloadCache(tmp_path / "Personal Budget.xlsx"),
        object(),
        tmp_path / "portfolio_payload.json",
        IdleMonitor(timeout_seconds=60),
        tmp_path / "Personal Budget.xlsx",
        budget_plan_cache=plan_cache,
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        status, _ = _request(base_url, "POST", "/api/post", {"posting_date": "2026-08-30"})
        assert status == 200
        status, _ = _request(base_url, "POST", "/api/journal/row", {
            "row_number": 3, "posting_date": "2026-08-31",
            "debit": "Food", "credit": "Cash", "amount": 10,
            "note": "Corrected", "expected": {},
        })
        assert status == 200
        status, result = _request(base_url, "POST", "/api/budget-plan/save", {"updates": [
            {"category": "Salary", "month": "2026-01", "value": 1000},
        ]})
        assert (status, result) == (200, {"ok": True})
        assert len(invalidations) == 3
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


@pytest.mark.parametrize("journal_method", ["_post", "_update_journal_row"])
def test_budget_save_serializes_with_journal_writes(tmp_path, monkeypatch, journal_method):
    from concurrent.futures import ThreadPoolExecutor
    from finance_hub import server

    budget_started = threading.Event()
    release_budget = threading.Event()
    journal_started = threading.Event()
    journal_called = threading.Event()

    def budget_write(*args):
        budget_started.set()
        assert release_budget.wait(5)

    class App:
        def post(self, *args, **kwargs):
            journal_called.set()
            return {}

        update_journal_row = post

    monkeypatch.setattr(server, "write_budget_plan", budget_write)
    handler_type = make_handler(
        App(), BudgetPayloadCache(tmp_path / "budget.xlsx"), object(),
        tmp_path / "portfolio.json", IdleMonitor(60), tmp_path / "budget.xlsx",
    )
    # Exercise the actual route methods on separate request-handler instances.
    budget_handler = object.__new__(handler_type)
    journal_handler = object.__new__(handler_type)

    def journal_write():
        journal_started.set()
        return getattr(journal_handler, journal_method)({
            "posting_date": "2026-01-05", "row_number": 3,
            "debit": "Primary Checking", "credit": "Salary", "amount": 950,
            "expected": {},
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        budget = pool.submit(budget_handler._budget_plan_save, {"updates": [
            {"category": "Salary", "month": "2026-01", "value": 1000},
        ]})
        try:
            assert budget_started.wait(5)
            journal = pool.submit(journal_write)
            assert journal_started.wait(5)
            assert not journal_called.wait(0.1)
        finally:
            release_budget.set()
        budget.result(timeout=5)
        journal.result(timeout=5)
    assert journal_called.is_set()


@pytest.fixture
def running_server(tmp_path):
    workbook_path = tmp_path / "Personal Budget.xlsx"
    build_fixture_workbook(workbook_path, _ACCOUNTS)
    journal_app = JournalApp.create(workbook_path, tmp_path / "journal_entry.sqlite")
    budget_cache = BudgetPayloadCache(workbook_path)
    portfolio_payload = tmp_path / "portfolio_payload.json"
    portfolio_job = PortfolioRefreshJob(tmp_path / "portfolio.xlsx", portfolio_payload)
    handler = make_handler(
        journal_app,
        budget_cache,
        portfolio_job,
        portfolio_payload,
        IdleMonitor(timeout_seconds=60),
        workbook_path,
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield base_url, journal_app
    finally:
        httpd.shutdown()
        httpd.server_close()
        journal_app.close()


@pytest.fixture
def running_close_server(tmp_path):
    workbook_path = tmp_path / "Personal Budget.xlsx"
    build_fixture_workbook(workbook_path, _CLOSE_ACCOUNTS)
    journal_app = JournalApp.create(workbook_path, tmp_path / "journal_entry.sqlite")
    budget_cache = BudgetPayloadCache(workbook_path)
    portfolio_payload = tmp_path / "portfolio_payload.json"
    portfolio_payload.write_text(json.dumps({
        "generated_at": "2026-08-31T18:00:00-04:00",
        "payload": {"as_of_date": "2026-08-31", "total_value_cad": 500.0},
    }), encoding="utf-8")
    portfolio_job = PortfolioRefreshJob(tmp_path / "portfolio.xlsx", portfolio_payload)
    handler = make_handler(
        journal_app,
        budget_cache,
        portfolio_job,
        portfolio_payload,
        IdleMonitor(timeout_seconds=60),
        workbook_path,
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield base_url, journal_app
    finally:
        httpd.shutdown()
        httpd.server_close()
        journal_app.close()


def _request(base_url, method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base_url + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_root_serves_the_shell_html(running_server):
    base_url, _ = running_server
    with urllib.request.urlopen(base_url + "/") as resp:
        assert resp.status == 200
        assert "text/html" in resp.headers["Content-Type"]
        assert b"<!doctype html>" in resp.read().lower()


def test_unlisted_asset_name_is_404(running_server):
    """Assets are served from an explicit allowlist, not an arbitrary
    filename lifted from the URL, so there's no path-traversal surface."""
    base_url, _ = running_server
    status, _ = _request(base_url, "GET", "/assets/../server.py")
    assert status == 404


def test_get_accounts(running_server):
    base_url, _ = running_server
    status, data = _request(base_url, "GET", "/api/accounts")
    assert status == 200
    assert "Food" in data["accounts"]
    assert data["kinds"]["Food"] == "expense"


def test_heartbeat(running_server):
    base_url, _ = running_server
    status, data = _request(base_url, "POST", "/api/heartbeat", {})
    assert status == 200
    assert data == {"ok": True}


def test_unknown_route_is_404(running_server):
    base_url, _ = running_server
    status, data = _request(base_url, "GET", "/api/nope")
    assert status == 404


def test_budget_caches_are_invalidated_after_post_and_successful_correction(tmp_path):
    class MutationApp:
        def post(self, posting_date):
            return {"posting_date": posting_date.isoformat()}

        def update_journal_row(self, row_number, **changes):
            return {"row_number": row_number, "amount": changes["amount"]}

    class TrackingCache:
        invalidations = 0

        def invalidate(self):
            self.invalidations += 1

    cache = TrackingCache()
    handler = make_handler(
        MutationApp(),
        cache,
        object(),
        tmp_path / "portfolio_payload.json",
        IdleMonitor(timeout_seconds=60),
        tmp_path / "Personal Budget.xlsx",
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        status, _ = _request(
            base_url, "POST", "/api/post", {"posting_date": "2026-08-30"}
        )
        assert status == 200
        status, _ = _request(base_url, "POST", "/api/journal/row", {
            "row_number": 3,
            "posting_date": "2026-08-31",
            "debit": "Food",
            "credit": "Cash",
            "amount": 10,
            "note": "Corrected",
            "expected": {},
        })
        assert status == 200
        assert cache.invalidations == 2
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_import_flow_via_http(running_server):
    base_url, _ = running_server
    csv_text = "Date,Description,Amount\n2026-08-01,EXAMPLE MARKET #1042,-41.20\n"
    status, sniff = _request(base_url, "POST", "/api/import/sniff", {"text": csv_text})
    assert status == 200
    assert sniff["known_source"] is None

    status, _ = _request(base_url, "POST", "/api/import/mapping", {
        "header_signature": sniff["header_signature"], "label": "test", "default_account": "Rewards Card",
        "mapping": {"date_col": "Date", "desc_col": "Description", "amount_col": "Amount"},
        "date_format": "%Y-%m-%d",
    })
    assert status == 200

    status, result = _request(base_url, "POST", "/api/import/commit", {
        "text": csv_text, "header_signature": sniff["header_signature"],
    })
    assert status == 200
    assert result["new"] == 1

    status, txs = _request(base_url, "GET", "/api/transactions")
    assert status == 200
    assert len(txs) == 1


def test_preview_dates_via_http(running_server):
    base_url, _ = running_server
    csv_text = "Date,Description,Amount\n08/25/2026,EXAMPLE MARKET,-41.20\nnot-a-date,CORNER STORE,-18.75\n"
    status, result = _request(base_url, "POST", "/api/import/preview-dates", {
        "text": csv_text, "date_col": "Date", "date_format": "%m/%d/%Y",
    })
    assert status == 200
    assert result["samples"] == [
        {"raw": "08/25/2026", "parsed": "2026-08-25"},
        {"raw": "not-a-date", "parsed": None},
    ]


def test_missing_required_field_returns_400(running_server):
    base_url, _ = running_server
    status, data = _request(base_url, "POST", "/api/import/sniff", {})
    assert status == 400
    assert "error" in data


def test_post_with_nothing_to_post_returns_400(running_server):
    base_url, _ = running_server
    status, data = _request(base_url, "POST", "/api/post", {"posting_date": "2026-08-30"})
    assert status == 400


def test_budget_payload_route_surfaces_workbook_errors(running_server):
    """The fixture workbook has Journal + Ledger but no Budget sheet — proves
    build_dashboard_payload's ValueError comes back as a 400, not a 500."""
    base_url, _ = running_server
    status, data = _request(base_url, "GET", "/api/budget/payload")
    assert status == 400
    assert "Budget" in data["error"]


def test_portfolio_payload_is_null_when_the_pipeline_has_never_run(running_server):
    base_url, _ = running_server
    status, data = _request(base_url, "GET", "/api/portfolio/payload")
    assert status == 200
    assert data["payload"] is None
    assert data["generated_at"] is None
    assert data["stale_days"] is None


def test_portfolio_status_starts_idle(running_server):
    base_url, _ = running_server
    status, data = _request(base_url, "GET", "/api/portfolio/status")
    assert status == 200
    assert data["state"] == "idle"
    assert data["log"] == []


def test_month_close_get_preview_stage_and_post_date_guard(running_close_server):
    base_url, _ = running_close_server
    status, initial = _request(base_url, "GET", "/api/month-close?month=2026-08")
    assert status == 200
    assert initial["closing_date"] == "2026-08-31"
    assert initial["reconciliations"]["investment"]["ending_balance"] == 500

    body = {
        "month": "2026-08",
        "investment": {"enabled": True, "ending_balance": 500},
        "transit": {"enabled": True, "ending_balance": 10},
    }
    status, preview = _request(base_url, "POST", "/api/month-close/preview", body)
    assert status == 200
    assert preview["reconciliations"]["transit"]["credit"] == "Transit"

    status, staged = _request(base_url, "POST", "/api/month-close/stage", body)
    assert status == 200
    assert staged["staged_count"] == 2

    status, txs = _request(base_url, "GET", "/api/transactions")
    assert status == 200
    assert {row["origin"] for row in txs} == {"month_close"}

    status, error = _request(
        base_url, "GET", "/api/post/preview?posting_date=2026-08-30"
    )
    assert status == 400
    assert "2026-08-31" in error["error"]

    status, posted = _request(
        base_url, "POST", "/api/post", {"posting_date": "2026-08-31"}
    )
    assert status == 200
    assert posted["line_count"] == 2


def test_undo_import_via_http(running_server):
    base_url, _ = running_server
    csv_text = "Date,Description,Amount\n2026-08-01,EXAMPLE MARKET #1042,-41.20\n"
    status, sniff = _request(base_url, "POST", "/api/import/sniff", {"text": csv_text})
    _request(base_url, "POST", "/api/import/mapping", {
        "header_signature": sniff["header_signature"], "label": "test", "default_account": "Rewards Card",
        "mapping": {"date_col": "Date", "desc_col": "Description", "amount_col": "Amount"},
        "date_format": "%Y-%m-%d",
    })
    status, commit = _request(base_url, "POST", "/api/import/commit", {
        "text": csv_text, "header_signature": sniff["header_signature"],
    })
    fingerprints = [row["fingerprint"] for row in commit["rows"]]

    status, undo = _request(base_url, "POST", "/api/import/undo", {"fingerprints": fingerprints})
    assert status == 200
    assert undo == {"deleted": 1, "kept": 0}

    status, txs = _request(base_url, "GET", "/api/transactions")
    assert txs == []


def test_undo_import_missing_fingerprints_returns_400(running_server):
    base_url, _ = running_server
    status, data = _request(base_url, "POST", "/api/import/undo", {})
    assert status == 400
    assert "error" in data


def test_batch_lines_and_update_journal_row_via_http(running_server):
    base_url, journal_app = running_server
    csv_text = "Date,Description,Amount\n2026-08-01,EXAMPLE MARKET #1042,-41.20\n"
    status, sniff = _request(base_url, "POST", "/api/import/sniff", {"text": csv_text})
    _request(base_url, "POST", "/api/import/mapping", {
        "header_signature": sniff["header_signature"], "label": "test", "default_account": "Rewards Card",
        "mapping": {"date_col": "Date", "desc_col": "Description", "amount_col": "Amount"},
        "date_format": "%Y-%m-%d",
    })
    status, commit = _request(base_url, "POST", "/api/import/commit", {
        "text": csv_text, "header_signature": sniff["header_signature"],
    })
    fingerprints = [row["fingerprint"] for row in commit["rows"]]
    _request(base_url, "POST", "/api/transactions/categorize", {
        "fingerprints": fingerprints, "category": "Food", "note": "Groceries",
    })
    status, post_result = _request(base_url, "POST", "/api/post", {"posting_date": "2026-08-30"})
    assert status == 200

    status, lines = _request(base_url, "GET", f"/api/batches/{post_result['batch_id']}/lines")
    assert status == 200
    assert len(lines) == 1
    original = lines[0]

    status, outcome = _request(base_url, "POST", "/api/journal/row", {
        "row_number": original["row_number"], "posting_date": "2026-08-31",
        "debit": "Food", "credit": "Rewards Card", "amount": 50.0,
        "note": "Corrected", "expected": original,
    })
    assert status == 200
    assert outcome["row_number"] == original["row_number"]

    status, lines_after = _request(base_url, "GET", f"/api/batches/{post_result['batch_id']}/lines")
    assert lines_after[0]["amount"] == 50.0
    assert lines_after[0]["note"] == "Corrected"


def test_update_journal_row_conflict_returns_409(running_server):
    base_url, _ = running_server
    csv_text = "Date,Description,Amount\n2026-08-01,EXAMPLE MARKET #1042,-41.20\n"
    status, sniff = _request(base_url, "POST", "/api/import/sniff", {"text": csv_text})
    _request(base_url, "POST", "/api/import/mapping", {
        "header_signature": sniff["header_signature"], "label": "test", "default_account": "Rewards Card",
        "mapping": {"date_col": "Date", "desc_col": "Description", "amount_col": "Amount"},
        "date_format": "%Y-%m-%d",
    })
    status, commit = _request(base_url, "POST", "/api/import/commit", {
        "text": csv_text, "header_signature": sniff["header_signature"],
    })
    fingerprints = [row["fingerprint"] for row in commit["rows"]]
    _request(base_url, "POST", "/api/transactions/categorize", {
        "fingerprints": fingerprints, "category": "Food", "note": "Groceries",
    })
    status, post_result = _request(base_url, "POST", "/api/post", {"posting_date": "2026-08-30"})
    status, lines = _request(base_url, "GET", f"/api/batches/{post_result['batch_id']}/lines")
    stale_expected = {**lines[0], "amount": lines[0]["amount"] + 1}

    status, data = _request(base_url, "POST", "/api/journal/row", {
        "row_number": lines[0]["row_number"], "posting_date": "2026-08-31",
        "debit": "Food", "credit": "Rewards Card", "amount": 50.0,
        "note": "Corrected", "expected": stale_expected,
    })
    assert status == 409
    assert "error" in data


def test_batch_lines_unknown_batch_returns_400(running_server):
    base_url, _ = running_server
    status, data = _request(base_url, "GET", "/api/batches/nope/lines")
    assert status == 400
    assert "error" in data
