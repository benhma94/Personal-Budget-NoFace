# Personal Finance Hub

I used to track all my personal finances in an Excel sheet, but picked up some skills during my PhD (plus AI help) to build a more friendly looking app (It's still Excel in the backend, dont' worry).

It includes classic accounting General Journals, Budgets, Reports, and also includes other finance tools, like portfolio andalysis/tracking and retirement projections. The backend excel book is used for storing data (I'm still an accountant at heart).

The app uses CAD as its base currency. Budget, Report, and Journal do not need LSEG Workspace. If you wish to use the portfolio tracking function, you will need access to LSEG Workspace - Yahoo Finance is used as a fallback for some instruments, but I find coverage can be lacking.

## Quick start

Clone the repo to your local folder.

Copy in a file into `data/` named `Personal Budget Template.xlsx`. There's a template available for you to get started in `examples/`

Double click `run.bat`. That will open a local webpage which will let you use the tool. It is not hosted online, just a local html page.

The `run.bat` file just runs the following:

```powershell
uv python install
Copy-Item 'examples/Personal Budget Template.xlsx' 'data/Personal Budget.xlsx'
.\run.bat
```

| Tab | What it does |
| --- | --- |
| **Report** | Compare spending with the budget and see cash flow and balances. |
| **Budget** | Edit monthly targets in the workbook. |
| **Journal** | Import bank CSVs, review transactions, and post entries to the workbook. |
| **Retirement** | Explore projections using spending and any cached portfolio value. |
| **Portfolio** | View cached holdings and returns; refresh with LSEG Workspace running. |

## Use your own data

Keep your workbook, exports, caches, and journal database under `data/` everything is kept locally and nothing is shared (unless you upload to Github for some reason).

The example workbook works with the included synthetic profile. For your own account and category names, copy `src/budget_dashboard/profile.example.json` to `data/profile.json` and update it to match your workbook.

For Portfolio, copy the synthetic template from 'examples/' to `data/`:

Edit its **Config** sheet to choose your start date, risk-free rate, and benchmark fallback weights. Portfolio refresh currently accepts Wealthsimple activities exports only; put one in `data/` if you use this feature. 

To fetch prices, run `setup_lseg_app_key.bat` once and keep LSEG Workspace open when you press **Refresh**. The Portfolio tab can still display its last cached run while Workspace is closed. 

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
