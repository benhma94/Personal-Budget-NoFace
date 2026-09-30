# User guide

Start with the [README](../README.md) to install `uv`, copy the example budget workbook, and launch the app. This guide covers setting up your own data and using the tabs.

## Use your own budget workbook

The app reads `data/Personal Budget.xlsx`. The included template has example Journal entries and budget targets so you can explore the app. Replace those entries before using its figures for your finances.

If you edit the workbook directly, enter Journal amounts as positive numbers and use the Debit and Credit columns to show direction. Budget income targets are positive; expense targets are negative. The Report tab needs at least one dated Journal entry.

If you change account or category names, copy `src/budget_dashboard/profile.example.json` to `data/profile.json` and update its labels to match your workbook. Budget, Report, Journal, and month close share this profile. The example workbook already matches the bundled profile.

Close the workbook in Excel before saving a budget or posting Journal entries in the app.

## Use the tabs

- **Budget:** Edit monthly targets, optionally fill blank cells with suggestions, and save. The app backs up the workbook before writing.
- **Report:** Compare actual spending with your budget and review cash flow, balances, and trends. It reads the workbook when you open the tab.
- **Journal:** Import a bank CSV, map its columns and account on the first import, then review and categorize transactions. Confirm transfers between your own accounts before posting to avoid counting them twice. Preview a batch before posting it to the workbook. The optional **Close** view helps reconcile investment and transit accounts at month-end.
- **Retirement:** Explore projections using budget spending and the latest cached portfolio value. You can change the assumptions; the projections are illustrative.
- **Portfolio:** Review holdings, performance, and exposure from the last refresh. Follow the setup below to refresh its data.

## Portfolio setup (optional)

The other tabs do not need a brokerage export. Portfolio can display cached results, but its current refresh importer accepts Wealthsimple activities exports only. Skip this setup if you do not use Portfolio.

1. Copy `examples/Portfolio Template.xlsx` to `portfolio.xlsx` in the project folder.
2. In its **Config** sheet, review the inception date, risk-free rate, and benchmark weights. Use **InstrumentMap** for ticker mappings and **PriceOverrides** for prices you maintain yourself, if needed.
3. Put an `activities-export-YYYY-MM-DD.csv` file in `data/`. The newest matching file is used.
4. Run `setup_lseg_app_key.bat` once to store your LSEG app key. Keep LSEG Workspace open, then press **Refresh** on the Portfolio tab.

Refresh updates the report sheets in `portfolio.xlsx` and the dashboard cache. You can still view the last cached results when Workspace is closed. Portfolio returns use time-weighted return, which removes contributions and withdrawals from the performance calculation.

Keep your personal workbook, exports, and local profile under `data/`. These files and `portfolio.xlsx` are ignored by Git.
