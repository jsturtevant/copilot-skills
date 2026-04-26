# install-instructions.ps1 — Windows variant of install-instructions.sh
param(
    [string]$Backend = "",
    [switch]$Global
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginDir = Split-Path -Parent $ScriptDir

# Auto-detect backend
if (-not $Backend) {
    $Backend = if ((Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -ErrorAction SilentlyContinue).State -eq 'Enabled') { "hyperlight" }
    else { "monty" }
}

Write-Host "Backend: $Backend"

# Preflight
& powershell.exe -File "$ScriptDir\preflight.ps1" -Backend $Backend
if ($LASTEXITCODE -ne 0) { throw "Preflight failed for backend $Backend" }

# Discover tools — persist manifest for hook consumption
$toolsFile = Join-Path $PluginDir ".codeact-tools.json"
python3 "$PluginDir\skills\${Backend}-codeact\scripts\codeact.py" --discover --output $toolsFile
Write-Host "Wrote: $toolsFile"

# Persist backend choice so runtime dispatch matches install
$backendMarker = Join-Path $PluginDir ".codeact-backend"
Set-Content -Path $backendMarker -Value $Backend -NoNewline
Write-Host "Wrote: $backendMarker"

$toolList = python3 -c "import json; d=json.load(open(r'$toolsFile')); print(', '.join(t['name'] for t in d.get('tools', [])))"

# Generate instructions reference
$toolRef = (python3 "$PluginDir\skills\${Backend}-codeact\scripts\codeact.py" --instructions | Out-String)

# Backend-specific syntax block
if ($Backend -eq "monty") {
    $syntax = @"
Tools are called as regular Python functions with keyword arguments:
``````python
content = view(path="README.md")
files = glob(pattern="**/*.py")
hits = grep(pattern="TODO", paths="src")
result = bash(command="git log --oneline -5")
``````
"@
} else {
    $syntax = @"
Tools are called via call_tool() with keyword arguments:
``````python
content = call_tool("view", path="README.md")
files = call_tool("glob", pattern="**/*.py")
hits = call_tool("grep", pattern="TODO", paths="src")
result = call_tool("bash", command="git log --oneline -5")
``````
"@
}

# Output paths
if ($Global) {
    $instrDir = "$env:USERPROFILE\.copilot"
    $instrFile = "$instrDir\codeact.instructions.md"
} else {
    $instrDir = ".github\instructions"
    $instrFile = "$instrDir\codeact.instructions.md"
}
$agentFile = Join-Path $PluginDir "agents\codeact.agent.md"
$agentTmpl = Join-Path $PluginDir "agents\codeact.agent.md.tmpl"

if (-not (Test-Path $agentTmpl)) { throw "Agent template not found: $agentTmpl" }

New-Item -ItemType Directory -Path $instrDir -Force | Out-Null

function Substitute($templatePath) {
    $content = Get-Content $templatePath -Raw
    $content = $content -replace '\{\{BACKEND\}\}', $Backend
    $content = $content -replace '\{\{CODEACT_DIR\}\}', $PluginDir
    $content = $content -replace '\{\{TOOL_LIST\}\}', $toolList
    # Literal replacement for multiline blocks (no regex metacharacter expansion)
    $content = $content.Replace('{{TOOL_REFERENCE}}', $toolRef)
    $content = $content.Replace('{{SYNTAX}}', $syntax)
    return $content
}

# Atomic-ish write: write temp then move
function AtomicWrite($targetPath, $content) {
    $tmp = "$targetPath.tmp"
    [System.IO.File]::WriteAllText($tmp, $content, [System.Text.UTF8Encoding]::new($false))
    Move-Item -Path $tmp -Destination $targetPath -Force
}

AtomicWrite $instrFile (Substitute (Join-Path $PluginDir "instructions\codeact.instructions.md.tmpl"))
Write-Host "Wrote: $instrFile"

AtomicWrite $agentFile (Substitute $agentTmpl)
Write-Host "Wrote: $agentFile"

Write-Host ""
Write-Host "CodeAct installed (backend=$Backend). Restart session to load."
