# NOFACE — Personal Budget

**NoFace** = **N**et-worth, **O**utflows, **F**inancial **A**ccounts, **C**ashflow & **E**xpenses.

NOFACE is a free personal finance app for Windows. It helps you track your
spending, plan a budget, see your net worth, follow your investments, and plan
for retirement.

It runs entirely on your own computer. Your financial data is never uploaded
anywhere — it stays in ordinary Excel files that you own.

## Install

1. Go to the [latest release](https://github.com/benhma94/Personal-Budget-NoFace/releases/latest)
   and download **`NOFACE-win-x64-Setup.exe`**.
2. Double-click the file to install it.
3. Windows may show a blue box saying it "protected your PC". This is normal
   for small apps that aren't from a big company. Click **More info**, then
   **Run anyway**.

That's it. NOFACE adds a shortcut to your desktop and Start menu, and opens in
your web browser. You don't need to install anything else.

## Getting started

NOFACE starts with example data already filled in, so you can click around and
see how everything works before adding your own.

Your files are kept in **Documents → NOFACE → data**:

- **`Personal Budget.xlsx`** — your transactions and budget.
- **`portfolio.xlsx`** — your investment settings.

When you're ready, replace the examples with your own information. Close the
files in Excel before saving changes from inside NOFACE.

To keep your files somewhere else (another folder, a shared drive, or a synced
folder), hold **Shift** while you open NOFACE and choose the folder that holds
`Personal Budget.xlsx`. NOFACE remembers it. If that folder can't be found the
next time, for example because a network drive isn't connected, NOFACE asks you
to choose again.

## What's inside

NOFACE has five tabs along the top of the window:

- **Report** — where your money went. See spending by category, how you did
  against your budget, cash flow over time, and your net worth. View a single
  month, the year so far, or compare with last year.
- **Budget** — set how much you plan to earn and spend in each category, month
  by month. NOFACE suggests amounts based on your past budgets and spending.
- **Journal** — add transactions without typing them in by hand. Download a
  CSV file from your bank's website and drop it in. NOFACE learns how you
  categorize things (for example, that "Loblaws" means Groceries), so it fills
  in more for you each time. It also spots transfers between your own accounts,
  like paying off a credit card, so they aren't counted twice.
- **Portfolio** — your investments: what you own, how it's spread across
  countries and industries, how it has performed compared with the market,
  and how risky it is.
- **Retirement** — an estimate of how long your savings could last, based on
  your current spending and investments. You can change the assumptions (such
  as growth, inflation, and when you retire) to explore different scenarios.
  This is a rough guide, not financial advice.

## Good to know

- **Backups:** Every time NOFACE saves to your Excel file, it first keeps a
  copy of the old version in a `backups` folder next to it.
- **Updates:** NOFACE checks for new versions when it starts. If one is
  available, it downloads quietly and installs the next time you close the
  app.
- **Uninstall:** Open Windows **Settings → Apps**, find NOFACE, and uninstall
  it. Your **Documents → NOFACE** folder is kept so you don't lose your data.
- **Live investment prices:** The Portfolio tab gets its market prices from
  LSEG Workspace, a paid financial data service, and reads your transactions
  from a Wealthsimple activities export, which you upload under **Journal →
  Import → Portfolio transactions**. Without these, the Portfolio tab
  won't have data to show; the other tabs work normally. Setup steps are in
  the [developer guide](app/DEVELOPMENT.md#optional-live-portfolio-data).

## For developers

To run NOFACE from the source code, change it, or learn how the calculations
work, see the [developer guide](app/DEVELOPMENT.md).

## License

MIT; see [LICENSE](LICENSE).
