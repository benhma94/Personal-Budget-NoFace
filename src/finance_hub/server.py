"""Local HTTP server for the finance hub: serves the shell UI and every
Budget / Portfolio / Retirement / Journal API route from one process.

Thin by design, following the pattern proven in journal_entry's original
server.py: every decision lives in the app objects (JournalApp,
BudgetPayloadCache, PortfolioRefreshJob); this module only translates
HTTP <-> Python calls and turns exceptions into JSON error responses. Binds
to 127.0.0.1 only — this is a single-user local tool, not a network service.
"""
from __future__ import annotations

import json
import threading
import webbrowser
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from journal_entry.app import JournalApp, RowConflictError
from journal_entry.csv_import import CsvFormatError
from journal_entry.xlsx_append import UnknownAccountError, VerificationError, WorkbookLockedError

from .budget_api import BudgetPayloadCache
from .budget_plan_api import BudgetPlanSourceCache, read_budget_plan, write_budget_plan
from .idle_monitor import IdleMonitor, start_idle_watcher
from .portfolio_api import PortfolioRefreshJob, read_cache, stale_days
from .retirement import build_retirement_defaults, forecast_retirement

_ASSET_DIR = Path(__file__).parent / "assets"
# Budget and Journal both replace the same ZIP package. Serialize the whole
# read/backup/write/verify operation across routes and browser tabs.
_WORKBOOK_WRITE_LOCK = threading.RLock()
_ASSET_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
# Explicit allowlist: static assets never take an arbitrary filename from the
# URL, so there's no path-traversal surface to worry about.
_ASSETS = {
    "shell.html",
    "theme.css", "hub.js",
    "budget.css", "budget.js",
    "budget-plan.css", "budget-plan.js",
    "retirement.css", "retirement.js",
    "portfolio.css", "portfolio.js",
    "journal.css", "journal.js",
}

_ERROR_STATUS: dict[type[Exception], HTTPStatus] = {
    KeyError: HTTPStatus.BAD_REQUEST,
    ValueError: HTTPStatus.BAD_REQUEST,
    FileNotFoundError: HTTPStatus.BAD_REQUEST,
    CsvFormatError: HTTPStatus.BAD_REQUEST,
    UnknownAccountError: HTTPStatus.BAD_REQUEST,
    WorkbookLockedError: HTTPStatus.CONFLICT,
    RowConflictError: HTTPStatus.CONFLICT,
    VerificationError: HTTPStatus.INTERNAL_SERVER_ERROR,
}


def _status_for(exc: Exception) -> HTTPStatus:
    for exc_type, status in _ERROR_STATUS.items():
        if isinstance(exc, exc_type):
            return status
    return HTTPStatus.INTERNAL_SERVER_ERROR


def _parse_date(value: str | None) -> date:
    if not value:
        raise ValueError("posting_date is required")
    return date.fromisoformat(value)


def make_handler(
    journal_app: JournalApp,
    budget_cache: BudgetPayloadCache,
    portfolio_job: PortfolioRefreshJob,
    portfolio_payload_path: str | Path,
    idle_monitor: IdleMonitor,
    workbook_path: str | Path,
    *,
    budget_plan_cache: BudgetPlanSourceCache | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Build a request-handler class closing over the hub's app objects."""

    payload_path = Path(portfolio_payload_path)
    budget_workbook_path = Path(workbook_path)
    plan_cache = budget_plan_cache if budget_plan_cache is not None else BudgetPlanSourceCache(budget_workbook_path)

    class Handler(BaseHTTPRequestHandler):
        server_version = "FinanceHub/1"

        def log_message(self, format: str, *args: object) -> None:
            pass  # keep the console quiet; failures still come back as JSON

        def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(self, body: bytes, content_type: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_asset(self, name: str) -> None:
            if name not in _ASSETS:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            asset_path = _ASSET_DIR / name
            self._send_bytes(asset_path.read_bytes(), _ASSET_MIME[asset_path.suffix])

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length))

        def _handle(self, fn) -> None:
            try:
                result = fn()
                self._send_json(result if result is not None else {"ok": True})
            except Exception as exc:  # noqa: BLE001 - convert every failure to JSON
                self._send_json({"error": str(exc)}, _status_for(exc))

        # ------------------------------------------------------- portfolio --
        def _portfolio_payload(self) -> dict:
            cached = read_cache(payload_path)
            if cached is None:
                return {"payload": None, "generated_at": None, "stale_days": None}
            return {
                "payload": cached.get("payload"),
                "generated_at": cached.get("generated_at"),
                "stale_days": stale_days(cached.get("generated_at")),
            }

        def _portfolio_refresh(self) -> dict:
            portfolio_job.start()
            return {"state": "running"}

        def _heartbeat(self) -> dict:
            idle_monitor.beat()
            return {"ok": True}

        def _month_close_portfolio(self) -> dict | None:
            cached = read_cache(payload_path)
            payload = cached.get("payload") if cached else None
            if not payload:
                return None
            return {
                "total_value_cad": payload.get("total_value_cad"),
                "as_of_date": payload.get("as_of_date"),
                "generated_at": cached.get("generated_at"),
            }

        def _month_close_preview(self, body: dict) -> dict:
            selection = {
                "investment": body.get("investment", {}),
                "transit": body.get("transit", {}),
            }
            return journal_app.month_close_preview(
                body["month"], self._month_close_portfolio(), selection
            )

        def _month_close_stage(self, body: dict) -> dict:
            selection = {
                "investment": body.get("investment", {}),
                "transit": body.get("transit", {}),
            }
            return journal_app.stage_month_close(
                body["month"], self._month_close_portfolio(), selection
            )

        # ---------------------------------------------------- budget plan --
        def _budget_plan_payload(self, year_param: str | None) -> dict:
            if not year_param:
                raise ValueError("year is required")
            year = int(year_param)
            if not 1900 <= year <= 9999:
                raise ValueError("year must be between 1900 and 9999")
            return read_budget_plan(budget_workbook_path, year, cache=plan_cache)

        def _budget_plan_save(self, body: dict) -> None:
            if not isinstance(body, dict) or not isinstance(body.get("updates"), list):
                raise ValueError("updates must be a list")
            updates = []
            for item in body["updates"]:
                if not isinstance(item, dict) or not {"category", "month", "value"} <= item.keys():
                    raise ValueError("Each update requires category, month, and value")
                updates.append((item["category"], item["month"], item["value"]))
            with _WORKBOOK_WRITE_LOCK:
                write_budget_plan(budget_workbook_path, updates)
                budget_cache.invalidate()
                plan_cache.invalidate()

        # ------------------------------------------------------ retirement --
        def _retirement_defaults(self) -> dict:
            cached = read_cache(payload_path)
            return build_retirement_defaults(
                budget_cache.get(),
                cached,
                portfolio_stale_days=stale_days(
                    cached.get("generated_at") if cached else None
                ),
            )

        def _retirement_forecast(self, body: dict) -> dict:
            cached = read_cache(payload_path)
            portfolio_payload = cached.get("payload") if cached else None
            raw_start = portfolio_payload.get("as_of_date") if portfolio_payload else None
            start = date.fromisoformat(raw_start) if raw_start else date.today()
            return forecast_retirement(body, start_date=start)

        # ------------------------------------------------------------ POST --
        def _post(self, body: dict) -> dict:
            with _WORKBOOK_WRITE_LOCK:
                result = journal_app.post(_parse_date(body["posting_date"]))
                budget_cache.invalidate()  # the Journal sheet just changed
                plan_cache.invalidate()
            return result

        def _update_journal_row(self, body: dict) -> dict:
            with _WORKBOOK_WRITE_LOCK:
                result = journal_app.update_journal_row(
                    body["row_number"], posting_date=_parse_date(body["posting_date"]),
                    debit=body["debit"], credit=body["credit"], amount=body["amount"],
                    note=body.get("note", ""), expected=body["expected"],
                )
                budget_cache.invalidate()  # the Journal sheet just changed
                plan_cache.invalidate()
            return result

        # ------------------------------------------------------------ GET --
        def do_GET(self) -> None:  # noqa: N802
            parts = urlsplit(self.path)
            path, query = parts.path, parse_qs(parts.query)
            if path == "/":
                self._send_asset("shell.html")
            elif path.startswith("/assets/"):
                self._send_asset(path[len("/assets/"):])
            elif path == "/api/budget/payload":
                self._handle(budget_cache.get)
            elif path == "/api/budget-plan/payload":
                self._handle(lambda: self._budget_plan_payload(query.get("year", [None])[0]))
            elif path == "/api/budget/transactions":
                self._handle(lambda: budget_cache.query_transactions(
                    start=query.get("start", [None])[0],
                    end=query.get("end", [None])[0],
                    category=query.get("category", [None])[0],
                    account=query.get("account", [None])[0],
                    kind=query.get("kind", [None])[0],
                    q=query.get("q", [None])[0],
                    limit=int(query.get("limit", [100])[0]),
                    offset=int(query.get("offset", [0])[0]),
                ))
            elif path == "/api/portfolio/payload":
                self._handle(self._portfolio_payload)
            elif path == "/api/portfolio/status":
                self._handle(portfolio_job.status)
            elif path == "/api/retirement/defaults":
                self._handle(self._retirement_defaults)
            elif path == "/api/accounts":
                self._handle(journal_app.accounts)
            elif path == "/api/transactions":
                statuses = query.get("status")
                statuses = statuses[0].split(",") if statuses else None
                account = query.get("account", [None])[0]
                self._handle(lambda: journal_app.list_transactions(status=statuses, account=account))
            elif path == "/api/rules":
                self._handle(journal_app.rules)
            elif path == "/api/transfers/suggested":
                self._handle(journal_app.suggested_transfers)
            elif path == "/api/month-close":
                self._handle(lambda: journal_app.month_close_preview(
                    query.get("month", [None])[0], self._month_close_portfolio()
                ))
            elif path == "/api/post/preview":
                self._handle(lambda: journal_app.post_preview(_parse_date(query.get("posting_date", [None])[0])))
            elif path == "/api/batches":
                self._handle(journal_app.batches)
            elif path.startswith("/api/batches/") and path.endswith("/lines"):
                batch_id = path[len("/api/batches/"):-len("/lines")]
                self._handle(lambda: journal_app.batch_lines(batch_id))
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        # ----------------------------------------------------------- POST --
        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            try:
                body = self._read_json()
            except (ValueError, UnicodeDecodeError) as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            if path == "/api/heartbeat":
                self._handle(self._heartbeat)
            elif path == "/api/portfolio/refresh":
                self._handle(self._portfolio_refresh)
            elif path == "/api/retirement/forecast":
                self._handle(lambda: self._retirement_forecast(body))
            elif path == "/api/budget-plan/save":
                self._handle(lambda: self._budget_plan_save(body))
            elif path == "/api/import/sniff":
                self._handle(lambda: journal_app.sniff(body["text"]))
            elif path == "/api/import/mapping":
                self._handle(lambda: journal_app.save_mapping(
                    body["header_signature"], body["label"], body["default_account"],
                    body["mapping"], body["date_format"],
                ))
            elif path == "/api/import/preview-dates":
                self._handle(lambda: journal_app.preview_dates(
                    body["text"], body["date_col"], body["date_format"],
                ))
            elif path == "/api/import/commit":
                self._handle(lambda: journal_app.import_csv(
                    body["text"], header_signature=body.get("header_signature"),
                    account=body.get("account"), mapping=body.get("mapping"),
                    date_format=body.get("date_format"),
                ))
            elif path == "/api/import/undo":
                self._handle(lambda: journal_app.undo_import(body["fingerprints"]))
            elif path == "/api/transactions/categorize":
                self._handle(lambda: journal_app.categorize(
                    body["fingerprints"], body["category"], body.get("note", ""),
                    learn=body.get("learn", True),
                ))
            elif path == "/api/transactions/status":
                self._handle(lambda: journal_app.set_status(body["fingerprints"], body["status"]))
            elif path == "/api/rules":
                self._handle(lambda: journal_app.add_rule(
                    body["pattern"], body["category"], body.get("note", ""),
                    match_type=body.get("match_type", "contains"),
                ))
            elif path == "/api/transfers/confirm":
                self._handle(lambda: journal_app.confirm_transfer(
                    body["outgoing_fingerprint"], body["incoming_fingerprint"], body.get("note", ""),
                ))
            elif path == "/api/transfers/unlink":
                self._handle(lambda: journal_app.unlink_transfer(body["fingerprint"]))
            elif path == "/api/month-close/preview":
                self._handle(lambda: self._month_close_preview(body))
            elif path == "/api/month-close/stage":
                self._handle(lambda: self._month_close_stage(body))
            elif path == "/api/post":
                self._handle(lambda: self._post(body))
            elif path == "/api/journal/row":
                self._handle(lambda: self._update_journal_row(body))
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        # --------------------------------------------------------- DELETE --
        def do_DELETE(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path.startswith("/api/rules/"):
                rule_id = int(path.rsplit("/", 1)[-1])
                self._handle(lambda: journal_app.delete_rule(rule_id))
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    return Handler


_IDLE_TIMEOUT_SECONDS = 60.0
_IDLE_CHECK_INTERVAL_SECONDS = 5.0


def run(
    workbook_path: str | Path,
    db_path: str | Path,
    *,
    portfolio_workbook: str | Path,
    portfolio_payload: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    journal_app = JournalApp.create(workbook_path, db_path)
    budget_cache = BudgetPayloadCache(workbook_path)
    portfolio_job = PortfolioRefreshJob(portfolio_workbook, portfolio_payload)
    idle_monitor = IdleMonitor(timeout_seconds=_IDLE_TIMEOUT_SECONDS)
    handler = make_handler(
        journal_app, budget_cache, portfolio_job, portfolio_payload, idle_monitor, workbook_path
    )
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    print(f"Finance hub running at {url}")
    print("Press Ctrl+C to stop.")
    stop_watching = start_idle_watcher(
        server, idle_monitor, check_interval_seconds=_IDLE_CHECK_INTERVAL_SECONDS
    )
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_watching.set()
        server.server_close()
        journal_app.close()
