"""Action block parser: extracts and replaces action blocks in LLM responses."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass
class ParsedAction:
    action: str
    payload: dict[str, object]
    start: int
    end: int


_ACTION_BLOCK_RE = re.compile(r"```action\n(.*?)\n```", re.DOTALL)


def parse_action_blocks(text: str) -> list[ParsedAction]:
    """Find all ```action\\n{...}\\n``` blocks in text.

    Returns a list of ParsedAction. For malformed JSON, returns a ParsedAction
    with action="error" and the parse error in payload.
    """
    results: list[ParsedAction] = []
    for match in _ACTION_BLOCK_RE.finditer(text):
        json_str = match.group(1)
        try:
            data = json.loads(json_str)
            action = data.get("action", "")
            payload = {k: v for k, v in data.items() if k != "action"}
            results.append(
                ParsedAction(
                    action=action,
                    payload=payload,
                    start=match.start(),
                    end=match.end(),
                )
            )
        except json.JSONDecodeError as e:
            results.append(
                ParsedAction(
                    action="error",
                    payload={"error": str(e), "raw": json_str},
                    start=match.start(),
                    end=match.end(),
                )
            )
    return results


def replace_action_block(text: str, parsed: ParsedAction, replacement: str) -> str:
    """Replace the action block at parsed.start:parsed.end with replacement text."""
    return text[: parsed.start] + replacement + text[parsed.end :]
