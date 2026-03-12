"""Tests for core/git_transfer.py using real temporary git repos."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.git_transfer import (
    GitTransferError,
    apply_patch,
    create_bundle,
    create_patch,
    extract_bundle,
)


def _make_repo(path: Path) -> None:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    (path / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True
    )


def _add_commit(path: Path, filename: str, content: str) -> None:
    (path / filename).write_text(content)
    subprocess.run(
        ["git", "add", filename], cwd=path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", f"add {filename}"],
        cwd=path,
        check=True,
        capture_output=True,
    )


class TestCreateBundle:
    def test_returns_bytes(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_repo(repo)
        result = create_bundle(str(repo))
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_bundle_starts_with_git_magic(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_repo(repo)
        result = create_bundle(str(repo))
        assert result.startswith(b"# v2 git bundle\n") or result.startswith(
            b"# v3 git bundle\n"
        )

    def test_raises_on_invalid_repo(self, tmp_path: Path) -> None:
        non_git_dir = tmp_path / "not_a_repo"
        non_git_dir.mkdir()
        with pytest.raises(GitTransferError):
            create_bundle(str(non_git_dir))


class TestExtractBundle:
    def test_produces_working_checkout(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_repo(repo)
        _add_commit(repo, "hello.txt", "hello world\n")

        bundle_bytes = create_bundle(str(repo))
        dest = tmp_path / "dest"
        extract_bundle(bundle_bytes, str(dest))

        assert (dest / ".git").exists()
        assert (dest / "hello.txt").exists()

    def test_extracted_repo_has_git_log(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_repo(repo)

        bundle_bytes = create_bundle(str(repo))
        dest = tmp_path / "dest"
        extract_bundle(bundle_bytes, str(dest))

        result = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=dest,
            capture_output=True,
            text=True,
            check=True,
        )
        lines = result.stdout.strip().splitlines()
        assert len(lines) > 0

    def test_raises_on_invalid_bundle(self, tmp_path: Path) -> None:
        dest = tmp_path / "dest"
        with pytest.raises(GitTransferError):
            extract_bundle(b"not a bundle", str(dest))


class TestCreatePatch:
    def test_returns_empty_string_when_no_changes(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_repo(repo)
        result = create_patch(str(repo))
        assert result == ""

    def test_returns_diff_for_unstaged_changes(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_repo(repo)
        _add_commit(repo, "tracked.txt", "original content\n")

        (repo / "tracked.txt").write_text("modified content\n")

        result = create_patch(str(repo))
        assert "@@" in result
        assert "tracked.txt" in result

    def test_returns_diff_for_staged_changes(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_repo(repo)
        _add_commit(repo, "file.txt", "original\n")

        (repo / "file.txt").write_text("changed\n")
        subprocess.run(
            ["git", "add", "file.txt"], cwd=repo, check=True, capture_output=True
        )

        result = create_patch(str(repo))
        assert result != ""


class TestApplyPatch:
    def test_noop_on_empty_string(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_repo(repo)
        apply_patch(str(repo), "")

    def test_applies_valid_patch(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        _make_repo(source)
        _add_commit(source, "target.txt", "original content\n")

        (source / "target.txt").write_text("modified content\n")
        subprocess.run(
            ["git", "add", "target.txt"],
            cwd=source,
            check=True,
            capture_output=True,
        )
        patch = create_patch(str(source))
        assert patch != ""

        dest = tmp_path / "dest"
        dest.mkdir()
        _make_repo(dest)
        _add_commit(dest, "target.txt", "original content\n")

        apply_patch(str(dest), patch)
        assert (dest / "target.txt").read_text() == "modified content\n"

    def test_raises_on_invalid_patch(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_repo(repo)
        with pytest.raises(GitTransferError):
            apply_patch(str(repo), "not a patch")
