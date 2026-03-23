"""Unit tests for web.action_parser."""

from __future__ import annotations

from web.action_parser import parse_action_blocks, replace_action_block


def test_should_parse_single_action_block() -> None:
    text = '```action\n{"action": "create_task", "title": "My Task"}\n```'
    results = parse_action_blocks(text)
    assert len(results) == 1
    assert results[0].action == "create_task"
    assert results[0].payload == {"title": "My Task"}
    assert results[0].start == 0
    assert results[0].end == len(text)


def test_should_parse_multiple_action_blocks() -> None:
    text = (
        'First\n```action\n{"action": "create_task", "title": "A"}\n```\n'
        'Second\n```action\n{"action": "create_feature", "title": "B", "description": "desc"}\n```'
    )
    results = parse_action_blocks(text)
    assert len(results) == 2
    assert results[0].action == "create_task"
    assert results[0].payload == {"title": "A"}
    assert results[1].action == "create_feature"
    assert results[1].payload == {"title": "B", "description": "desc"}


def test_should_handle_malformed_json() -> None:
    text = '```action\n{not valid json}\n```'
    results = parse_action_blocks(text)
    assert len(results) == 1
    assert results[0].action == "error"
    assert "error" in results[0].payload
    assert "raw" in results[0].payload


def test_should_handle_no_action_blocks() -> None:
    text = "Just some plain text with no action blocks."
    results = parse_action_blocks(text)
    assert results == []


def test_should_replace_action_block_with_confirmation() -> None:
    text = '```action\n{"action": "create_task", "title": "My Task"}\n```'
    parsed = parse_action_blocks(text)[0]
    result = replace_action_block(text, parsed, "✓ Created task: My Task (id: abc-123)")
    assert result == "✓ Created task: My Task (id: abc-123)"


def test_should_handle_action_block_with_surrounding_text() -> None:
    text = 'Here is what I will do:\n```action\n{"action": "create_task", "title": "T"}\n```\nDone.'
    results = parse_action_blocks(text)
    assert len(results) == 1
    replacement = "✓ Created task: T"
    result = replace_action_block(text, results[0], replacement)
    assert result == "Here is what I will do:\n✓ Created task: T\nDone."
