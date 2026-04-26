#!/usr/bin/env bash
# detect-backend.sh — auto-pick codeact backend at install time.
#   macOS            → monty
#   Linux + /dev/kvm → hyperlight
#   Linux + /dev/mshv → hyperlight
#   Linux (neither)  → monty
#   Windows + Hyper-V → hyperlight (via PowerShell check)
#   Windows (no HV)  → monty
set -euo pipefail

case "$(uname -s)" in
  Darwin)
    echo "monty"
    ;;
  Linux)
    if [[ -r /dev/kvm ]] || [[ -r /dev/mshv ]]; then
      echo "hyperlight"
    else
      echo "monty"
    fi
    ;;
  MINGW*|MSYS*|CYGWIN*)
    # Check for Hyper-V via PowerShell
    if powershell.exe -NoProfile -Command \
        "(Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V).State -eq 'Enabled'" \
        2>/dev/null | grep -qi true; then
      echo "hyperlight"
    else
      echo "monty"
    fi
    ;;
  *)
    echo "monty"
    ;;
esac
