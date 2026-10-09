[CmdletBinding()]
param(
    # Also copy NoFace.lnk to this user's desktop.
    [switch]$Desktop,
    # Rebuild the NAS runtime even if its stamp is current.
    [switch]$Force
)

# One-time (and after dependency changes) setup for NOFACE on this PC:
#   1. build the shared runtime in <repo>\app\.runtime (only when uv.lock/.python-version changed)
#   2. mirror it to %LOCALAPPDATA%\NoFace\runtime so Python imports from local disk
#   3. put the NAS host in this user's Local Intranet zone (no security prompts)
#   4. (re)create <repo>\NoFace.lnk
# Run with: powershell -ExecutionPolicy Bypass -File app\setup.ps1 [-Desktop] [-Force]

$ErrorActionPreference = 'Stop'

# $root is the app folder; the shortcut goes one level up, in the repo root.
$root = $PSScriptRoot
$repoRoot = Split-Path -Parent $root
$runtime = Join-Path $root '.runtime'
$nasStampFile = Join-Path $runtime 'stamp.txt'
$local = Join-Path $env:LOCALAPPDATA 'NoFace\runtime'
$localStampFile = Join-Path $local 'stamp.txt'

function Write-Step([string]$message) {
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Assert-ExitCode([string]$what) {
    if ($LASTEXITCODE -ne 0) {
        throw "$what failed (exit code $LASTEXITCODE)."
    }
}

function Read-Stamp([string]$path) {
    if (Test-Path -LiteralPath $path) {
        return (Get-Content -LiteralPath $path -Raw).Trim()
    }
    return ''
}

# Stamp = SHA-256 (hex) of the raw bytes of uv.lock followed immediately by the
# raw bytes of .python-version. launch.pyw only compares stamp files as strings,
# so the formula just has to be consistent between runs of this script.
function Get-RuntimeStamp {
    $stream = New-Object System.IO.MemoryStream
    try {
        foreach ($name in @('uv.lock', '.python-version')) {
            $bytes = [System.IO.File]::ReadAllBytes((Join-Path $root $name))
            $stream.Write($bytes, 0, $bytes.Length)
        }
        $stream.Position = 0
        return (Get-FileHash -InputStream $stream -Algorithm SHA256).Hash.ToLowerInvariant()
    } finally {
        $stream.Dispose()
    }
}

function Remove-IfExists([string]$path) {
    if (Test-Path -LiteralPath $path) {
        # uv's python store contains a junction (cpython-3.12-... -> cpython-3.12.N-...).
        # Unlink junctions first: PS 5.1's Remove-Item -Recurse can follow them.
        Get-ChildItem -LiteralPath $path -Directory -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } |
            ForEach-Object { [System.IO.Directory]::Delete($_.FullName, $false) }
        Remove-Item -LiteralPath $path -Recurse -Force
    }
}

function Invoke-Mirror([string]$from, [string]$to) {
    New-Item -ItemType Directory -Force -Path $to | Out-Null
    & robocopy $from $to /MIR /NFL /NDL /NJH /NJS /NP /R:1 /W:1 /MT:16 | Out-Null
    # robocopy: 0-7 = success (bit flags for copied/extra/mismatched), >= 8 = failure.
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed (exit code $LASTEXITCODE)."
    }
    $global:LASTEXITCODE = 0
}

# ---------------------------------------------------------------------------
# Step 1: NAS master runtime
# ---------------------------------------------------------------------------
$stamp = Get-RuntimeStamp
$nasStamp = Read-Stamp $nasStampFile

if ($Force -or $nasStamp -ne $stamp) {
    Write-Step "Building shared runtime in $runtime (this can take a few minutes)"

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv is not installed or is not on PATH. It is needed to (re)build the shared runtime. Install it from https://docs.astral.sh/uv/ and run setup.ps1 again."
    }

    $pythonVersion = (Get-Content -LiteralPath (Join-Path $root '.python-version') -Raw).Trim()
    # Build on local disk, then copy to the share: uv's python store needs
    # junctions, which SMB shares can't hold, and thousands of small writes are
    # far faster locally than over the network.
    $build = Join-Path $env:TEMP 'noface-runtime-build'
    $pythonDir = Join-Path $build 'python'
    $sitePackages = Join-Path $build 'site-packages'
    $pythonStore = Join-Path $build 'python-store'
    $requirements = Join-Path $build 'requirements.txt'

    # Invalidate first so an interrupted build is never mistaken for a good one.
    Remove-IfExists $nasStampFile
    Remove-IfExists $build
    New-Item -ItemType Directory -Force -Path $build, $runtime | Out-Null

    # OneDrive's cloud filter driver can break uv's default hardlink installs.
    $env:UV_LINK_MODE = 'copy'

    Push-Location $root
    try {
        Write-Host "    Installing standalone Python $pythonVersion"
        # --no-bin / --no-registry: don't add shims to ~/.local/bin or register
        # this interpreter in the Windows registry.
        & uv python install $pythonVersion --install-dir $pythonStore --no-bin --no-registry
        Assert-ExitCode 'uv python install'

        $installed = @(Get-ChildItem -LiteralPath $pythonStore -Directory |
            Where-Object {
                $_.Name -like "cpython-$pythonVersion*-windows-*" -and
                -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -and
                (Test-Path -LiteralPath (Join-Path $_.FullName 'pythonw.exe'))
            })
        if ($installed.Count -ne 1) {
            throw "Expected one CPython $pythonVersion install in $pythonStore, found $($installed.Count)."
        }
        # Move to a fixed path, then drop the store: it only holds uv bookkeeping.
        Move-Item -LiteralPath $installed[0].FullName -Destination $pythonDir
        Remove-IfExists $pythonStore

        Write-Host "    Exporting locked dependencies"
        & uv export --frozen --no-dev --no-emit-project --no-hashes --quiet -o $requirements
        Assert-ExitCode 'uv export'
        # The project itself is not installed (launch.pyw puts <repo>\app\src on
        # sys.path); drop any editable/self reference defensively.
        $lines = Get-Content -LiteralPath $requirements | Where-Object { $_ -notmatch '^\s*-e\s' -and $_.Trim() -ne '.' }
        Set-Content -LiteralPath $requirements -Value $lines -Encoding ASCII

        Write-Host "    Installing packages into site-packages"
        # --compile-bytecode: ship .pyc files so first launch on each PC is fast.
        & uv pip install --target $sitePackages --python (Join-Path $pythonDir 'python.exe') --compile-bytecode --quiet -r $requirements
        Assert-ExitCode 'uv pip install'
    } finally {
        Pop-Location
    }

    Write-Host "    Copying runtime to the share"
    Invoke-Mirror $build $runtime
    Remove-IfExists $build

    # Written last, so a half-finished build is retried next time.
    Set-Content -LiteralPath $nasStampFile -Value $stamp -Encoding ASCII -NoNewline
    $nasStamp = $stamp
    Write-Host "    Shared runtime built."
} else {
    Write-Step "Shared runtime is up to date"
}

# ---------------------------------------------------------------------------
# Step 2: local mirror
# ---------------------------------------------------------------------------
if ((Read-Stamp $localStampFile) -ne $nasStamp) {
    Write-Step "Copying runtime to $local"
    Invoke-Mirror $runtime $local
} else {
    Write-Step "Local runtime copy is up to date"
}

# ---------------------------------------------------------------------------
# Step 3: Local Intranet zone for the NAS host
# ---------------------------------------------------------------------------
$uncRoot = $null
if ($root.StartsWith('\\')) {
    $uncRoot = $root
} else {
    $qualifier = Split-Path -Path $root -Qualifier
    $letter = $qualifier.TrimEnd(':')
    $drive = Get-PSDrive -Name $letter -PSProvider FileSystem -ErrorAction SilentlyContinue
    if ($drive -and $drive.DisplayRoot -and $drive.DisplayRoot.StartsWith('\\')) {
        $uncRoot = $drive.DisplayRoot
    } else {
        $disk = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='$qualifier'" -ErrorAction SilentlyContinue
        if ($disk -and $disk.ProviderName) {
            $uncRoot = $disk.ProviderName
        }
    }
}

if (-not $uncRoot) {
    Write-Step "Repo is on a local drive; skipping Intranet zone setup"
} else {
    $nasHost = $uncRoot.TrimStart('\').Split('\')[0]
    $zoneMap = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings\ZoneMap'
    $parsed = $null
    $isIPv4 = [System.Net.IPAddress]::TryParse($nasHost, [ref]$parsed) -and
        $parsed.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork

    if ($isIPv4) {
        $key = Join-Path $zoneMap 'Ranges\NoFaceNAS'
        $current = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
        if ($current -and $current.':Range' -eq $nasHost -and $current.file -eq 1) {
            Write-Step "$nasHost is already in the Local Intranet zone"
        } else {
            Write-Step "Adding $nasHost to the Local Intranet zone (current user)"
            New-Item -Path $key -Force | Out-Null
            New-ItemProperty -LiteralPath $key -Name ':Range' -Value $nasHost -PropertyType String -Force | Out-Null
            New-ItemProperty -LiteralPath $key -Name 'file' -Value 1 -PropertyType DWord -Force | Out-Null
        }
    } else {
        $key = Join-Path $zoneMap "Domains\$nasHost"
        $current = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
        if ($current -and $current.file -eq 1) {
            Write-Step "$nasHost is already in the Local Intranet zone"
        } else {
            Write-Step "Adding $nasHost to the Local Intranet zone (current user)"
            New-Item -Path $key -Force | Out-Null
            New-ItemProperty -LiteralPath $key -Name 'file' -Value 1 -PropertyType DWord -Force | Out-Null
        }
    }
}

# ---------------------------------------------------------------------------
# Step 4: shortcut
# ---------------------------------------------------------------------------
$lnkPath = Join-Path $repoRoot 'NoFace.lnk'
Write-Step "Creating $lnkPath"
# Literal env var so the same .lnk resolves to each user's own local mirror.
$pythonw = '%LOCALAPPDATA%\NoFace\runtime\python\pythonw.exe'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = 'launch.pyw'
$shortcut.WorkingDirectory = $root
$shortcut.Description = 'NOFACE'
$shortcut.IconLocation = "$pythonw,0"
$shortcut.Save()
[void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)

if ($Desktop) {
    $desktopDir = [Environment]::GetFolderPath('Desktop')
    Copy-Item -LiteralPath $lnkPath -Destination (Join-Path $desktopDir 'NoFace.lnk') -Force
    Write-Step "Copied shortcut to $desktopDir"
}

Write-Host ""
Write-Host "Done. Launch NOFACE with NoFace.lnk" -ForegroundColor Green
