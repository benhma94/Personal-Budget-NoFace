# Personal Finance Hub

I used to track all my personal finances in an Excel sheet, but picked up some skills during my PhD (plus AI help) to build a more friendly looking app (It's still Excel Journal in the backend, dont' worry).

It includes classic accounting General Journals, Budgets, Reports, and also includes other finance tools, like portfolio andalysis/tracking and retirement projections. The backend excelbook is used for storing data (still an accountant at heart).

The app uses CAD as its base currency. Budget, Report, and Journal do not need LSEG Workspace. Portfolio refresh uses LSEG Workspace for market data, with Yahoo Finance as a fallback for individual instruments.

## Quick start (Windows)

Install `uv`, then run these commands in PowerShell from this folder:

```powershell
uv python install
Copy-Item 'examples/Personal Budget Template.xlsx' 'data/Personal Budget.xlsx'
.\run.bat
```

`run.bat` opens the hub at `http://127.0.0.1:8765`.

Or you could just double click the run.bat file, which does the exact same thing. Remember to copy in the workbook into the data/ folder.

There's a template available for you to get started.

The app keeps its Python environment in local AppData, outside this folder.

| Tab | What it does |
| --- | --- |
| **Budget** | Edit monthly targets in the workbook. |
| **Report** | Compare spending with the budget and see cash flow and balances. |
| **Journal** | Import bank CSVs, review transactions, and post entries to the workbook. |
| **Retirement** | Explore projections using spending and any cached portfolio value. |
| **Portfolio** | View cached holdings and returns; refresh with LSEG Workspace running. |

## Use your own data

Keep your workbook, exports, caches, and journal database under `data/`; they are ignored by Git. Before committing, review what is staged. The example workbook works with the included synthetic profile. For your own account and category names, copy `src/budget_dashboard/profile.example.json` to `data/profile.json` and update it to match your workbook.

For Portfolio, copy the synthetic template to the project root:

```powershell
Copy-Item 'examples/Portfolio Template.xlsx' 'portfolio.xlsx'
```

Edit its **Config** sheet to choose your start date, risk-free rate, and benchmark fallback weights. Add a Wealthsimple activities export to `data/`. To fetch prices, run `setup_lseg_app_key.bat` once and keep LSEG Workspace open when you press **Refresh**. The Portfolio tab can still display its last cached run while Workspace is closed. Your `portfolio.xlsx` is ignored by Git.

See the [user guide](docs/guide.md) for workbook setup, tab workflows, and portfolio configuration.

## Development

The project pins Python in `.python-version`. To install test dependencies and run the suite:

```powershell
$env:UV_PROJECT_ENVIRONMENT = "$env:LOCALAPPDATA\uv\project-envs\personal-budget"
uv sync --extra dev
uv run --frozen pytest
```

Tests use synthetic data and do not need LSEG Workspace.

## License

MIT; see [LICENSE](LICENSE).
