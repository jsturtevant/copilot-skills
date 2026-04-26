---
name: codeact-install-hyperlight
description: |
  Switch codeact to the Hyperlight backend (micro-VM sandbox via WebAssembly,
  stronger isolation). Use when user wants to switch to hyperlight, needs
  full Python support, or stronger sandbox isolation.
---

# codeact-install-hyperlight

Switch codeact to the **Hyperlight** backend (micro-VM sandbox).

```bash
bash "$SKILL_DIR/run.sh"
```

Runs preflight for hyperlight (requires KVM/mshv/Hyper-V), rediscovers tools,
and rewrites the instructions and agent files with backend pinned to `hyperlight`.

**Note:** Hyperlight is not supported on macOS. Use monty on macOS.
