# pre-tool-use.ps1 — Windows PreToolUse hook for codeact enforcement
$ErrorActionPreference = "SilentlyContinue"

$Mode = if ($env:CODEACT_MODE) { $env:CODEACT_MODE } else { "off" }

# Fast path
if ($Mode -eq "off" -or $Mode -eq "") {
    $null = [Console]::In.ReadToEnd()
    exit 0
}

$InputRaw = [Console]::In.ReadToEnd()
$InputObj = $InputRaw | ConvertFrom-Json

$ToolName = if ($InputObj.toolName) { $InputObj.toolName } elseif ($InputObj.tool_name) { $InputObj.tool_name } else { "" }
$ToolArgs = if ($InputObj.toolInput) { $InputObj.toolInput | ConvertTo-Json -Compress } elseif ($InputObj.input) { $InputObj.input | ConvertTo-Json -Compress } else { "{}" }

function Test-CodeActCall {
    if ($ToolName -ne "bash" -and $ToolName -ne "shell") { return $false }
    return $ToolArgs -match 'codeact\.py|scripts/codeact'
}

# Read discovered tool list from install-time manifest
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginDir = Split-Path -Parent $ScriptDir
$ToolsFile = Join-Path $PluginDir ".codeact-tools.json"
$InstalledTools = "view, create, edit, glob, bash, sql"
if (Test-Path $ToolsFile) {
    try {
        $manifest = Get-Content $ToolsFile -Raw | ConvertFrom-Json
        $InstalledTools = ($manifest.tools | ForEach-Object { $_.name }) -join ', '
    } catch {}
}

$DenyReason = @"
CodeAct enforcement active (CODEACT_MODE=$Mode). Collapse this work into one sandboxed Python run:

  bash plugins/codeact/scripts/codeact --code '<your python>'

Sandbox tools: ${InstalledTools}.
Disable enforcement: unset CODEACT_MODE.
"@

function Send-Deny {
    @{ permissionDecision = "deny"; permissionDecisionReason = $DenyReason } | ConvertTo-Json -Compress
    exit 0
}

function Send-Allow {
    Write-Output '{}'
    exit 0
}

$CounterFile = "$env:TEMP\codeact-$PID.count"

switch ($Mode) {
    "nudge" {
        if (Test-CodeActCall) { "0" | Set-Content $CounterFile; Send-Allow }
        $readOnly = @("view", "glob", "grep", "rg", "read_file", "file_search")
        if ($readOnly -contains $ToolName) {
            $count = if (Test-Path $CounterFile) { [int](Get-Content $CounterFile) } else { 0 }
            $count++
            "$count" | Set-Content $CounterFile
            if ($count -ge 3) { "0" | Set-Content $CounterFile; Send-Deny }
        } else {
            "0" | Set-Content $CounterFile
        }
        Send-Allow
    }
    "exclusive" {
        if (Test-CodeActCall) { Send-Allow }
        Send-Deny
    }
    default { Send-Allow }
}
