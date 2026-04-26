"""Example custom CodeAct tool: shout(text) → uppercases text.

Drop this file into ~/.config/codeact/tools/ to register a `shout` tool.
"""

TOOL = {
    "name": "shout",  # optional; defaults to filename stem
    "description": "Echo back input text in uppercase.",
    "parameters": {
        "text": {"type": "string", "required": True,
                 "description": "Text to shout."},
    },
    # "function": "run",  # optional; defaults to "run"
}


def run(text: str = "") -> str:
    return text.upper()
