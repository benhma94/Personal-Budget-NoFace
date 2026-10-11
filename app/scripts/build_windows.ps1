[CmdletBinding()]
param(
    # Markdown shown in the installer and the update feed. Release builds pass
    # the reviewed notes; local trial builds get a placeholder.
    [string]$ReleaseNotesFile = ''
)

# Builds the installable NOFACE for Windows:
#   1. PyInstaller one-folder bundle  -> build\bundle\NOFACE\NOFACE.exe
#   2. Velopack installer and update package -> dist\updates\<version>\
# Run with: powershell -ExecutionPolicy Bypass -File app\scripts\build_windows.ps1
# Needs uv and the .NET 8 SDK (for the pinned vpk tool in ..\.config\dotnet-tools.json).

$ErrorActionPreference = 'Stop'

$app = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent $app
$icon = Join-Path $app 'src\finance_hub\assets\noface.ico'
$project = Get-Content -LiteralPath (Join-Path $app 'pyproject.toml') -Raw
if ($project -notmatch '(?m)^version = "(\d+\.\d+\.\d+)"\r?$') {
    throw 'A three-part project version is required in pyproject.toml.'
}
$version = $Matches[1]
$toolManifest = Join-Path $repoRoot '.config\dotnet-tools.json'
$vpkVersion = (Get-Content -LiteralPath $toolManifest -Raw | ConvertFrom-Json).tools.vpk.version
if ($project -notmatch ('"velopack==' + [regex]::Escape($vpkVersion) + '[;"]')) {
    throw "The velopack Python package and the vpk tool ($vpkVersion) must have the same version."
}

$work = Join-Path $app 'build\pyinstaller'
$bundleRoot = Join-Path $app 'build\bundle'
$bundle = Join-Path $bundleRoot 'NOFACE'
$output = Join-Path $app "dist\updates\$version"
if (Test-Path -LiteralPath $output) {
    throw "Release output already exists; move it aside or choose a new version: $output"
}
foreach ($path in @($work, $bundleRoot)) {
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
}

Push-Location $app
try {
    Write-Host "==> PyInstaller bundle for NOFACE $version" -ForegroundColor Cyan
    # The module, not the pyinstaller.exe launcher: uv launchers fail to resolve
    # their script path when the checkout is on a network share.
    & uv run --locked --group release python -m PyInstaller --noconfirm `
        --workpath $work --distpath $bundleRoot packaging\noface.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }
} finally {
    Pop-Location
}
foreach ($file in @(
    'NOFACE.exe',
    '_internal\finance_hub\assets\shell.html',
    '_internal\examples\Personal Budget Template.xlsx',
    '_internal\examples\Portfolio Template.xlsx'
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $bundle $file) -PathType Leaf)) {
        throw "Bundle is missing a required file: $file"
    }
}

New-Item -ItemType Directory -Path $output -Force | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding $false
$notes = Join-Path $output 'RELEASE_NOTES.md'
if ($ReleaseNotesFile) {
    Copy-Item -LiteralPath $ReleaseNotesFile -Destination $notes
} else {
    [IO.File]::WriteAllText($notes, "Local trial build of NOFACE $version.", $utf8)
}

Write-Host "==> Velopack installer and update package" -ForegroundColor Cyan
& dotnet tool restore --tool-manifest $toolManifest
if ($LASTEXITCODE -ne 0) { throw 'Could not restore the pinned vpk tool.' }
Push-Location $repoRoot
try {
    & dotnet tool run vpk -- pack --packId NOFACE --packVersion $version `
        --packDir $bundle --mainExe NOFACE.exe --packTitle NOFACE `
        --icon $icon `
        --channel win-x64 --runtime win-x64 --delta None --noPortable --yes `
        --releaseNotes $notes --outputDir $output
    if ($LASTEXITCODE -ne 0) { throw "Velopack packaging failed with exit code $LASTEXITCODE." }
} finally {
    Pop-Location
}

[IO.File]::WriteAllText((Join-Path $output 'UNSIGNED-PERSONAL-BUILD.txt'),
    "Unsigned personal Windows build. Publisher identity is not code-signed. SHA-256 hashes verify file consistency, not publisher authenticity.`n", $utf8)
$hashes = Get-ChildItem -LiteralPath $output -File | Where-Object Name -ne 'SHA256SUMS' |
    Sort-Object Name | ForEach-Object {
        "$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant())  $($_.Name)"
    }
[IO.File]::WriteAllLines((Join-Path $output 'SHA256SUMS'), [string[]]$hashes, $utf8)
Write-Host "Built $output" -ForegroundColor Green
