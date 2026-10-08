# Personal Budget — NoFace

**NoFace** = **N**et-worth, **O**utflows, **F**inancial **A**ccounts, **C**ashflow & **E**xpenses.

A local, Windows-based personal finance hub. Double-click `run.bat` and a
browser tab opens with five tabs: **Portfolio**, **Budget**, **Report**,
**Retirement**, and **Journal**. It reads your own Excel workbooks, imports
bank CSVs into a categorized journal, tracks budget vs actual, and reports
portfolio performance and risk against a blended benchmark. All data stays in
the project folder on your machine.

## Quick start (5 minutes)

**Requirements:** Windows 10 or later and [uv](https://docs.astral.sh/uv/getting-started/installation/)
(`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`).
LSEG Workspace is optional and only needed for live portfolio prices.

```powershell
git clone https://github.com/benhma94/Personal-Budget-NoFace.git
cd Personal-Budget-NoFace
uv python install
$env:UV_PROJECT_ENVIRONMENT = "$env:LOCALAPPDATA\uv\project-envs\personal-budget"
uv sync
Copy-Item 'examples/Personal Budget Template.xlsx' 'data/Personal Budget.xlsx'
Copy-Item 'examples/Portfolio Template.xlsx' 'portfolio.xlsx'
```

Then double-click **`run.bat`**. It opens `http://127.0.0.1:8765`.

The Budget, Report, Journal, and Retirement tabs work right away with the
synthetic example data. The Portfolio tab shows data only after its first
**Refresh** (see [Optional: live portfolio data](#optional-live-portfolio-data)).

## Personal finance hub

`run.bat` is the single entry point. The Budget, Report, and Journal tabs use
your workbook. The Portfolio tab renders the last cached pipeline run (fast, and
works even with LSEG Workspace closed) and has its own **Refresh** button that
reruns the pipeline described below in the background. Press it after opening
LSEG Workspace when you want current prices.

The Portfolio tab shows positions, geographic/sector/asset-class exposure,
annualized time-weighted return vs an allocation-matched blended benchmark, and
a full risk suite (StDev, VaR 95/99%, Sharpe, Beta, Correlation, Information
Ratio, Treynor, Jensen's Alpha). Base currency: CAD.

The Retirement tab combines annualized year-to-date spending from the Budget
workbook with the cached current Portfolio value. It projects conservative,
base, and optimistic fixed-return paths in today's CAD, with editable growth,
inflation, retirement-income, and effective withdrawal-tax assumptions. These
inputs are remembered in the browser until **Reset to live data** is pressed.
Expected nominal growth defaults to the annualized money-weighted
since-inception return shown on the Portfolio tab, with 5% used only when that
portfolio history is unavailable.
An informational source tile shows the current TFSA value plus the cost base
of the non-registered account, both as a dollar total and as a share of the
portfolio. It does not change the forecast's effective tax-rate input.
The retirement start date can be delayed. Before that date, the portfolio is
projected to grow without withdrawals or additional contributions; the
60-year runway and spending withdrawals begin on the selected retirement date.
The projection is illustrative and does not model market sequence risk or
account-specific tax and withdrawal rules.

To launch without `run.bat`:

```powershell
$env:UV_PROJECT_ENVIRONMENT = "$env:LOCALAPPDATA\uv\project-envs\personal-budget"
uv run --frozen finance-hub
uv run --frozen finance-hub --workbook "data/Personal Budget.xlsx" --portfolio-workbook portfolio.xlsx --port 8765
```

## Development setup

```powershell
uv python install
$env:UV_PROJECT_ENVIRONMENT = "$env:LOCALAPPDATA\uv\project-envs\personal-budget"
uv sync --extra dev
```

The project pins Python in `.python-version` and requires a uv-managed interpreter. Its virtual environment is kept in local AppData instead of the project folder, so every computer gets its own machine-local environment. The batch launchers set `UV_PROJECT_ENVIRONMENT` automatically. When running `uv` manually in a new PowerShell session, set the variable as shown above first. Run commands through `uv run`; activating the environment is optional.

### Using from multiple computers

If you keep the project folder on a network share, each computer needs a one-time setup:

1. Install `uv`. On first launch, `run.bat` builds that computer's environment from `uv.lock`.
2. If you use live portfolio data, run `setup_lseg_app_key.bat`. The key is stored per Windows user.
3. If git reports "dubious ownership", add the share path to that user's `safe.directory`.

Run Finance Hub on one computer at a time, and close `Personal Budget.xlsx` in Excel on the other computer. The SQLite files in `data/` are not safe for simultaneous writers over SMB.

## Local account profile

The quick start copies the synthetic workbook into the ignored data folder:

```powershell
Copy-Item 'examples/Personal Budget Template.xlsx' 'data/Personal Budget.xlsx'
```

The template has five clearly labeled example Journal entries, one month of
budget targets, and zero opening balances. Replace the examples with your own
entries before using the figures. Keep Journal dates as dates and amounts as
positive numbers; use the Debit and Credit columns to indicate direction.
Budget income targets are positive and expense targets are negative. Add
month-end dates in Budget row 13 and keep category labels in rows 16–47.
The Report tab needs at least one dated Journal entry. The Portfolio tab uses
a separate `portfolio.xlsx` workbook.

Copy `src/budget_dashboard/profile.example.json` to `data/profile.json`, then
replace the example categories, account names, aliases, and month-close
accounts with the exact labels in your workbook. The local profile is ignored
by Git. Report, Budget, Journal, and month close use the same profile, so an
account classified as an asset or liability keeps that classification across
the app. The two month-close accounts must be listed under `asset_accounts`.
The example workbook already matches the synthetic profile; when you rename
an account or category, update both the workbook and profile.
Set `FINANCE_PROFILE_PATH` to an alternative JSON path if you run the app from
outside the project folder. If no local profile exists, the synthetic example
profile is used; customize it before relying on reports from a real workbook.

Keep the workbook, transaction exports, market-data caches, and merchant seed
rules under `data/`. Those files, root-level workbook exports, and backups are
ignored by Git. Review `git status` before every commit.

## Optional: live portfolio data

The Portfolio tab needs LSEG Workspace for market data, an LSEG app key, and a
Wealthsimple activities export for your transactions. LSEG Workspace is the
primary source for prices, FX, metadata, and Lipper fund data. Yahoo Finance is
used only as a per-instrument fallback.

Run `setup_lseg_app_key.bat` and enter the key in its masked prompt. The helper
stores `LSEG_APP_KEY` in the Windows user environment without putting the value
in this repository, shell history, or console output. `run.bat` loads the
persisted value directly, so it works immediately without signing out.

Keep LSEG Workspace running while generating reports. The app key is read from
the environment and is never written to the workbook or cache.

Drop a Wealthsimple activities export into
`data/activities-export-YYYY-MM-DD.csv`. The tool auto-detects the most recent
file in `data/`, or accepts an explicit path through `--transactions-csv`.

Copy `src/portfolio_tracker/symbols.example.json` to `data/symbols.json` to
configure ticker exceptions for your transactions: bare U.S. listings,
provider-to-price-source aliases, and instruments priced through the workbook's
PriceOverrides sheet. `PORTFOLIO_SYMBOLS_PATH` selects an alternative JSON file.
The local JSON file is ignored by Git.

Then press **Refresh** on the Portfolio tab (see [Run](#run)).

## Configure the workbook

Open `portfolio.xlsx` in the project folder and maintain the **Config**, **PriceOverrides**, and
**InstrumentMap** sheets. Config includes the base currency, risk-free rate,
inception date, classification cache TTL, and benchmark blend. InstrumentMap
maps canonical tickers to LSEG RICs and Yahoo fallback symbols.

The benchmark uses direct LSEG indices: `.TRGSPTSE` (S&P/TSX Composite Total
Return) for Canadian equity, `.SPXTR` (S&P 500 Total Return)
for U.S. equity, `.FTDLMSXNA` (FTSE Developed All Cap ex North America) for
other international equity, and `.FT26045CADT` (FTSE Canada Universe Bond
Total Return Index, CAD) for debt. Legacy `.GSPTSE`, `XIC.TO`, `^GSPC`,
`XAW.TO`, and `XBB.TO` Config entries are automatically translated to these
LSEG RICs. If direct index history is unavailable, the Canadian sleeve falls
back to adjusted `XIC.TO` prices and the debt sleeve falls back to `XBB.TO`.
On each report run, component weights are matched to the portfolio's current
look-through allocation. Cash, other or unknown asset classes, unknown equity
geography, and unidentifiable holdings are excluded from the denominator before
the weights are normalized to 100%. This is a current-allocation benchmark, not
a perfect historical-allocation benchmark: weights reflect the current
identifiable allocation and are rebalanced daily.

The configured inception date is a lower-bound preference. If it predates the
first imported transaction, reporting begins on the first transaction date.

## Run

Press **Refresh** on the Portfolio tab of the hub (see "Personal finance hub"
above) with LSEG Workspace open. That runs the same pipeline described below
in the background, streams its console output into the tab, and re-renders
the dashboard when it finishes — the Excel reports and the cached dashboard
payload are both updated in place.

For scripting, or to run the pipeline without the hub, `portfolio-tracker` is
still a standalone console script that validates `uv` is available, runs the
processing pipeline, updates the Excel reports, and writes the cached
dashboard payload:

```powershell
uv run --frozen portfolio-tracker
uv run --frozen portfolio-tracker --refresh-lseg
uv run --frozen portfolio-tracker --as-of 2026-08-25
```

The default run uses cached market data where valid. Pass `--refresh-lseg`
only when you want to force fresh Lipper allocation snapshots.

Each run reads the newest `data/activities-export-*.csv`, updates these five
`Report_*` sheets in `portfolio.xlsx`, and caches the dashboard payload to
`data/portfolio_payload.json` (read by the hub's Portfolio tab):

- `Report_Positions` — current holdings, last price, FX, CAD value, weight
- `Report_Exposure` — geographic / sector / asset-class breakdown with ETF lookthrough
- `Report_Performance` — TWR (YTD, 1Y, 3Y, 5Y, since-inception) vs an allocation-matched blended benchmark; horizons of one year or longer are annualized
- `Report_Risk` — annualized risk suite vs benchmark
- `Report_Meta` — run timestamp, data through date, warnings

User-maintained sheets are never modified.

The Portfolio tab includes an interactive time-weighted-return window. Choose
custom start and end dates, or use the YTD/1Y/3Y/5Y/since-inception shortcuts,
to compare annualized portfolio and benchmark TWR, with cumulative TWR shown on
the chart. Contributions and withdrawals, including cash and in-kind security
transfers, are identified and removed from portfolio return; same-day movements
between tracked accounts net to zero. The portfolio
value chart follows the same selected window. The holdings table can be
filtered by Wealthsimple account type, and the account breakdown includes each
account's own security units and transaction-derived cash balance.

The tab also includes the full YTD/1Y/3Y/5Y/since-inception performance
table and detailed TWR-based risk statistics: volatility, VaR at 95%
and 99%, probability of loss, Sharpe, beta, correlation, information ratio,
Treynor ratio, and Jensen's alpha.

## Tests

```powershell
$env:UV_PROJECT_ENVIRONMENT = "$env:LOCALAPPDATA\uv\project-envs\personal-budget"
uv run --frozen pytest
```

Tests use a `FakeDataSource` fixture — no network access required.

## Return methodology

Performance reports use **time-weighted return**. Contributions and withdrawals
are removed from daily returns, so portfolio performance is not distorted by
the size or timing of external cash flows. YTD is reported cumulatively; 1Y, 3Y,
5Y, and since-inception results are annualized using their actual calendar-day
length. Risk statistics such as volatility, Sharpe ratio, and beta use the same
daily TWR series with 252-trading-day risk annualization.

## Budget planning

Open **Budget** to edit one year of the workbook's Budget sheet. Income and
expense categories each have twelve monthly cells; amounts retain the sheet's
sign convention (positive income, negative expenses). Unsaved cells show
dimmed suggestions based on prior budgets and recent actual-versus-budget
differences. Hover over a cell for its prior-year budget and actual.

Type values or choose **Fill blanks with suggestions**, then **Save changes**.
Filling suggestions preserves values you already typed. Changes remain local
until saved; switching years or tabs asks before discarding them. Emptying an
input cancels its staged edit rather than deleting a saved workbook value.
Saving creates a timestamped backup beside the workbook in `backups/`, verifies
the written values, and extends month columns when needed. Close the workbook
in Excel before saving. Return to **Report** to see the updated budget totals.

## Personal budget dashboard

Place `Personal Budget.xlsx` in `data/`, then open the Report tab of the hub
(`run.bat`). It reads the workbook live on every visit; it never saves or
changes the Excel file. The dashboard uses the Journal, Budget, and Ledger sheets and
includes selectable month, YTD, year-over-year, and custom month-range views,
budget variance, cash-flow and net-worth trends, category detail, and balances.
The YoY view can compare either the selected month or year-to-date totals with
the equivalent period in the prior year.

`budget-dashboard` remains available as a standalone console script — useful
for scripting or debugging the payload the hub serves — and writes that same
payload as JSON instead of opening a browser:

```powershell
$env:UV_PROJECT_ENVIRONMENT = "$env:LOCALAPPDATA\uv\project-envs\personal-budget"
uv run --frozen budget-dashboard
uv run --frozen budget-dashboard --workbook "data/Personal Budget.xlsx" --output budget_dashboard.json
```

## Journal entry (CSV import)

Open the Journal tab of the hub (`run.bat`) for entering transactions instead
of typing them into Excel by hand. It never requires opening the workbook in
Excel; close it there first if it's open.

- **Import** — drop a bank CSV in. The first time a bank's column layout is
  seen, you map its Date/Description/Amount (or separate money-out/money-in)
  columns and pick which account it belongs to; that mapping is remembered by
  the file's header row and reused automatically afterward. Already-imported
  rows (matched by account + date + amount + description) are skipped and
  reported, so re-uploading an overlapping statement is safe.
- **Review** — a sortable, filterable list of imported transactions. Rows
  matching a learned rule arrive pre-filled with a suggested category, marked
  "Suggested" until you confirm or edit them. Confirming a category reinforces
  or creates a rule, so later imports need less manual work over time.
- **Transfers** — matching amounts with opposite signs across two of your own
  accounts (e.g. a credit-card payment) are suggested as a single transfer;
  confirming posts one line instead of two, avoiding double-counting.
- **Close** — choose a month to reconcile the investment and transit accounts
  named in your local profile at calendar month-end. The investment target is
  prefilled from the cached Portfolio total (with its as-of date shown and an
  editable override); enter the remaining transit balance manually. The assistant
  includes categorized transactions and confirmed transfers waiting in the
  Post queue, then previews optional `Investment Income` and `Transit`
  adjustments. Resolve every unconfirmed Review row first. Staged adjustments
  flow into the normal Post preview and must be posted on that month-end date.
- **Post** — pick one posting date, preview the aggregated Journal lines
  (grouped by account/category, same as manual entries), and post. Posting
  backs up the workbook first, edits only the Journal sheet's XML directly —
  never a full Excel load/save — and verifies every appended row reads back
  correctly afterward, restoring the backup automatically if anything looks
  wrong. Charts, tables, comments, and the Debit/Credit dropdowns are
  untouched.
- **Rules** — view, add, and delete the learned merchant → category rules.

An optional `data/merchant_seed_rules.json` can initialize rules in a new
Journal database. It contains a JSON list of `[pattern, category, note]`
entries and stays local with the other private data. Without it, a new
database starts with no merchant rules and learns them through Review.

The Journal tab is served by the same `finance-hub` process as the Budget and
Portfolio tabs — see "Personal finance hub" above for the launch commands.
Posting a batch immediately invalidates the Report tab's cache, so switching
back to Report shows the newly posted lines without a restart.

Imported transactions, learned rules, and remembered CSV layouts are stored
locally in `data/journal_entry.sqlite` (gitignored). Each post keeps a
timestamped backup of the workbook in `data/backups/`.

## License

MIT; see `LICENSE`.
