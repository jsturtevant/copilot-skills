---
name: codeact-install
description: |
  Install or reconfigure codeact. Auto-detects best backend (monty or hyperlight),
  runs preflight checks, discovers available tools, and writes configuration files.
  Use when user asks to install, configure, set up, or reconfigure codeact.
  Supports --global flag for system-wide install.
---

# codeact-install

Install or reconfigure the codeact plugin. Detects the best backend,
verifies it works, discovers available tools, and writes configuration.

## Usage

Run the install script from this skill's directory:

```bash
bash "$SKILL_DIR/run.sh"
```

For global install (applies to all repos):

```bash
bash "$SKILL_DIR/run.sh" --global
```

The script will:
1. Auto-detect the best backend (monty or hyperlight)
2. Run preflight checks to verify the backend works
3. Discover available tools
4. Write `.github/instructions/codeact.instructions.md` (or `$HOME/.copilot/` with --global)
5. Update the codeact agent file

After install, restart the session to load the new configuration.
