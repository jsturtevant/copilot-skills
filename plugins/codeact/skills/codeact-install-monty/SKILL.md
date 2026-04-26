---
name: codeact-install-monty
description: |
  Switch codeact to the Monty backend (Pydantic Monty — minimal Python interpreter
  in Rust, sub-microsecond startup). Use when user wants to switch to monty,
  use monty backend, or needs lightweight sandboxing.
---

# codeact-install-monty

Switch codeact to the **Monty** backend (Pydantic Monty).

```bash
bash "$SKILL_DIR/run.sh"
```

Runs preflight for monty, rediscovers tools, and rewrites the instructions
and agent files with backend pinned to `monty`.
