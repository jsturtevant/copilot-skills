# preflight.ps1 — Windows preflight check for codeact backends
param(
    [Parameter(Mandatory=$true)]
    [string]$Backend
)

$ErrorActionPreference = "Stop"

function Fail($msg) {
    Write-Error "PREFLIGHT FAIL ($Backend): $msg"
    exit 1
}

function Warn($msg) {
    Write-Warning "PREFLIGHT WARN ($Backend): $msg"
}

# Shared checks
try { $null = Get-Command python3 -ErrorAction Stop } catch { Fail "python3 not found" }

$pyVer = python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$pyParts = $pyVer -split '\.'
$pyMajor = [int]$pyParts[0]
$pyMinor = [int]$pyParts[1]

if ($pyMajor -lt 3 -or ($pyMajor -eq 3 -and $pyMinor -lt 10)) {
    Fail "Python $pyVer too old. Need >=3.10."
}

$hasUv = $null -ne (Get-Command uv -ErrorAction SilentlyContinue)

switch ($Backend) {
    "monty" {
        if (-not $hasUv) { Warn "uv not found. Will try pip fallback." }
        try {
            python3 -c "import pydantic_monty" 2>$null
        } catch {
            if ($hasUv) {
                Write-Host "pydantic-monty not installed. Will auto-install via uv." -ForegroundColor Yellow
            } else {
                Warn "pydantic-monty not installed. Install: pip install pydantic-monty"
            }
        }
    }
    "hyperlight" {
        if ($pyMajor -gt 3 -or ($pyMajor -eq 3 -and $pyMinor -gt 13)) {
            Fail "Python $pyVer too new for hyperlight Wasm. Need <=3.13."
        }
        $hvEnabled = (Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -ErrorAction SilentlyContinue).State -eq 'Enabled'
        if (-not $hvEnabled) {
            Fail "Hyper-V not enabled. Hyperlight needs hardware virtualization."
        }
        if (-not $hasUv) { Warn "uv not found. Will try pip fallback." }
    }
    default {
        Fail "Unknown backend: $Backend. Expected 'monty' or 'hyperlight'."
    }
}

Write-Host "Preflight OK: $Backend (Python $pyVer, uv=$hasUv)" -ForegroundColor Green
exit 0
