"""PyInstaller one-folder Windows build of NOFACE. Run from app\ (see scripts\build_windows.ps1)."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path.cwd()
source = root / "src"
datas = [
    (str(source / "finance_hub" / "assets"), "finance_hub/assets"),
    (str(source / "budget_dashboard" / "profile.example.json"), "budget_dashboard"),
    (str(source / "portfolio_tracker" / "symbols.example.json"), "portfolio_tracker"),
    (str(root / "examples" / "Personal Budget Template.xlsx"), "examples"),
    (str(root / "examples" / "Portfolio Template.xlsx"), "examples"),
    *collect_data_files("lseg.data"),
]
# launch.pyw imports these lazily, so PyInstaller cannot see them on its own.
hiddenimports = [
    "velopack",
    *collect_submodules("finance_hub"),
    *collect_submodules("portfolio_tracker"),
    *collect_submodules("budget_dashboard"),
    *collect_submodules("journal_entry"),
    *collect_submodules("lseg.data"),
]

analysis = Analysis(
    [str(root / "launch.pyw")],
    pathex=[str(source)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tkinter"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="NOFACE",
    console=False,
)
app = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="NOFACE",
)
