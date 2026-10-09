[CmdletBinding()]
param()

$secureKey = Read-Host "Paste your LSEG Workspace App Key" -AsSecureString
$keyPointer = [IntPtr]::Zero

try {
    $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    $appKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
    if ([string]::IsNullOrWhiteSpace($appKey)) {
        throw "No app key was entered. Nothing was changed."
    }

    [Environment]::SetEnvironmentVariable("LSEG_APP_KEY", $appKey, "User")
    $env:LSEG_APP_KEY = $appKey
    Write-Host "LSEG_APP_KEY was stored in your Windows user environment." -ForegroundColor Green
    Write-Host "The key was not written to this project or displayed on screen."
} finally {
    if ($keyPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
    Remove-Variable appKey -ErrorAction SilentlyContinue
}

Read-Host "Press Enter to close"
