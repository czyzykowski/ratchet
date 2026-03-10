"""Interactive feature creation script with Claude-assisted high-level spec generation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from core.store import Store


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactively create a feature with high-level specs via Claude."
    )
    parser.add_argument("--project-id", required=True, help="Project UUID")
    return parser.parse_args()


def run_claude(prompt: str, local_path: str, debug: bool = False) -> str:
    """Run claude -p with the given prompt, stream output to terminal, and return full output."""
    if debug:
        print("\n--- DEBUG: PROMPT SENT TO CLAUDE ---", file=sys.stderr)
        print(prompt, file=sys.stderr)
        print("--- END PROMPT ---\n", file=sys.stderr)

    cmd = ["claude", "-p", prompt, "--allowedTools", "Read,Glob,WebSearch,Bash"]
    proc = subprocess.Popen(
        cmd,
        cwd=local_path,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
    )

    output_lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)
        output_lines.append(line)

    proc.wait()
    return "".join(output_lines)


def build_initial_prompt(intent_md: str, feature_description: str) -> str:
    return f"""You are helping design a software feature for the Ratchet project.

## Project Intent
{intent_md}

## Your Role
Help the user think through this feature by asking clarifying questions one at a time.
Questions should be specific and concrete. Only one question per message. No preamble.

After sufficient clarification, when you have a clear picture of the feature,
produce the feature definition in this exact format:

## FEATURE READY
# Feature: <title>

## Description
<1-3 paragraph description of the feature>

## High-Level Specs
List each high-level spec as a numbered item. For each spec include:
- Title
- Order (integer, 1-based)
- Content: detailed description of what this spec should implement
- Dependencies: comma-separated indices of other specs this depends on (or "none")

Example format:
### 1. <Spec Title>
**Order:** 1
**Dependencies:** none
**Content:**
<detailed description>

### 2. <Spec Title>
**Order:** 2
**Dependencies:** 1
**Content:**
<detailed description>

Be specific about file paths, function names, and implementation requirements.
Read the codebase to understand current patterns before generating specs.

## User's Opening Description

{feature_description}"""


def build_continuation_prompt(
    initial_prompt: str, history: list[dict[str, str]], user_input: str
) -> str:
    return f"""{initial_prompt}

## Conversation History

{json.dumps(history, indent=2)}

## Latest User Message

{user_input}"""


def build_generation_prompt(
    initial_prompt: str, history: list[dict[str, str]], user_input: str
) -> str:
    gen_instruction = (
        "The user has indicated they are ready to generate the feature definition. "
        "Produce the feature definition now in the exact format specified, "
        "preceded by '## FEATURE READY' on its own line."
    )
    return f"""{initial_prompt}

## Conversation History

{json.dumps(history, indent=2)}

## Latest User Message

{user_input}

{gen_instruction}"""


def extract_feature_block(output: str) -> str:
    marker = "## FEATURE READY"
    idx = output.find(marker)
    if idx == -1:
        return ""
    return output[idx + len(marker) :].strip()


def parse_feature_block(block: str) -> tuple[str, str, list[dict]]:
    """Parse the FEATURE READY block into (title, description, high_level_specs).

    Returns (title, description, specs) where specs is a list of dicts with
    keys: title, order, content, dependencies (list of int indices).
    """
    # Extract feature title
    title_match = re.search(r"^#\s+Feature:\s+(.+)$", block, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else "Untitled Feature"

    # Extract description
    desc_match = re.search(
        r"##\s+Description\s*\n(.*?)(?=##\s+High-Level Specs|$)", block, re.DOTALL
    )
    description = desc_match.group(1).strip() if desc_match else ""

    # Extract high-level specs
    specs_match = re.search(r"##\s+High-Level Specs\s*\n(.*?)$", block, re.DOTALL)
    specs_block = specs_match.group(1).strip() if specs_match else ""

    specs: list[dict] = []
    # Split on ### N. pattern
    spec_pattern = re.compile(r"###\s+(\d+)\.\s+(.+?)(?=###\s+\d+\.|$)", re.DOTALL)
    for m in spec_pattern.finditer(specs_block):
        order = int(m.group(1))
        spec_title = m.group(2).strip().split("\n")[0].strip()
        spec_body = m.group(2)

        order_m = re.search(r"\*\*Order:\*\*\s*(\d+)", spec_body)
        if order_m:
            order = int(order_m.group(1))

        deps_m = re.search(r"\*\*Dependencies:\*\*\s*(.+)", spec_body)
        raw_deps = deps_m.group(1).strip() if deps_m else "none"
        dep_indices: list[int] = []
        if raw_deps.lower() != "none":
            for d in re.split(r"[,\s]+", raw_deps):
                d = d.strip()
                if d.isdigit():
                    dep_indices.append(int(d))

        content_m = re.search(r"\*\*Content:\*\*\s*\n(.*?)(?=\*\*|\Z)", spec_body, re.DOTALL)
        content = content_m.group(1).strip() if content_m else spec_body.strip()

        specs.append(
            {
                "title": spec_title,
                "order": order,
                "content": content,
                "dep_indices": dep_indices,
            }
        )

    return title, description, specs


async def persist_feature(
    project_id: UUID,
    title: str,
    description: str,
    specs: list[dict],
    store: Store,
) -> None:
    """Persist the feature and its high-level specs to the database."""
    from core.feature_manager import FeatureManager

    fm = FeatureManager(store)
    feature = await fm.create_feature(project_id, title, description)
    print(f"\nCreated feature: {feature.title}")
    print(f"Feature ID: {feature.id}")

    from core.models import HighLevelSpec

    # Create specs in order so that dependencies (always lower-order) are available inline.
    hls_by_order: dict[int, HighLevelSpec] = {}
    for spec_def in sorted(specs, key=lambda s: s["order"]):
        dep_indices = spec_def.get("dep_indices", [])
        dep_uuids = []
        for idx in dep_indices:
            dep_hls = hls_by_order.get(idx)
            if dep_hls is not None:
                dep_uuids.append(dep_hls.id)
            else:
                print(
                    f"  Warning: dep index {idx} not found for spec [{spec_def['order']}]",
                    file=sys.stderr,
                )

        hls = await fm.add_high_level_spec(
            feature_id=feature.id,
            title=spec_def["title"],
            order=spec_def["order"],
            content=spec_def["content"],
            dependencies=dep_uuids,
        )
        hls_by_order[spec_def["order"]] = hls
        dep_note = f" (deps: {dep_indices})" if dep_indices else ""
        print(f"  Added spec [{spec_def['order']}]: {spec_def['title']}{dep_note}")

    print(f"\nFeature created with {len(specs)} high-level spec(s).")
    print(f"Feature ID: {feature.id}")


async def _handle_feature_ready(
    output: str,
    project_id: UUID,
    store: Store,
) -> bool:
    """Check for ## FEATURE READY marker and handle save confirmation."""
    if "## FEATURE READY" not in output:
        return False

    block = extract_feature_block(output)
    title, description, specs = parse_feature_block(block)

    print("\n--- Feature Summary ---")
    print(f"Title: {title}")
    desc_text = description[:200] + "..." if len(description) > 200 else description
    print(f"Description: {desc_text}")
    print(f"High-level specs: {len(specs)}")
    for s in specs:
        deps = s.get("dep_indices", [])
        dep_str = f" (deps: {deps})" if deps else ""
        print(f"  [{s['order']}] {s['title']}{dep_str}")

    try:
        confirm = input("\nSave this feature? [y/n] ").strip().lower()
    except KeyboardInterrupt:
        print("\nSession ended, feature not saved.")
        sys.exit(0)

    if confirm != "y":
        return False

    await persist_feature(project_id, title, description, specs, store)
    return True


async def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    args = parse_args()
    debug = bool(os.environ.get("RATCHET_DEBUG"))

    try:
        project_id = UUID(args.project_id)
    except ValueError:
        print(f"Error: invalid project-id: {args.project_id!r}", file=sys.stderr)
        sys.exit(1)

    if not shutil.which("claude"):
        print("Error: claude binary not found in PATH.", file=sys.stderr)
        sys.exit(1)

    from core.db import close_pool
    from core.project_manager import ProjectManager
    from core.store import PostgresStore

    store = PostgresStore()
    try:
        pm = ProjectManager(store)
        project = await pm.get_project(project_id)
        if project is None:
            print(f"Error: project {project_id} not found.", file=sys.stderr)
            sys.exit(1)

        local_path = project.local_path
        intent_md_path = Path(local_path) / "docs" / "INTENT.md"
        if not intent_md_path.exists():
            print(f"Error: INTENT.md not found at {intent_md_path}", file=sys.stderr)
            sys.exit(1)

        intent_md = intent_md_path.read_text()

        print(f"Project: {project.name} ({local_path})")
        print("Describe the feature you want to build:")

        try:
            feature_description = input("> ").strip()
        except KeyboardInterrupt:
            print("\nSession ended.")
            sys.exit(0)

        if not feature_description:
            print("Error: feature description cannot be empty.", file=sys.stderr)
            sys.exit(1)

        initial_prompt = build_initial_prompt(intent_md, feature_description)
        history: list[dict[str, str]] = []

        print("\n--- Claude ---")
        output = run_claude(initial_prompt, local_path, debug)
        history.append({"role": "user", "content": feature_description})
        history.append({"role": "assistant", "content": output})

        if await _handle_feature_ready(output, project_id, store):
            return

        while True:
            try:
                user_input = input("\n> ").strip()
            except KeyboardInterrupt:
                print("\nSession ended, feature not saved.")
                sys.exit(0)

            if not user_input:
                continue

            is_trigger = user_input.lower() in ("done", "generate", "go")

            if is_trigger:
                prompt = build_generation_prompt(initial_prompt, history, user_input)
            else:
                prompt = build_continuation_prompt(initial_prompt, history, user_input)

            print("\n--- Claude ---")
            output = run_claude(prompt, local_path, debug)

            if await _handle_feature_ready(output, project_id, store):
                break

            if is_trigger:
                print(
                    "\nWarning: ## FEATURE READY marker not found. Continuing conversation."
                )

            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": output})

    except KeyboardInterrupt:
        print("\nSession ended, feature not saved.")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
