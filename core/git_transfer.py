"""Git bundle and patch utilities for transferring repository state."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


class GitTransferError(Exception):
    pass


def create_bundle(repo_path: str) -> bytes:
    result = subprocess.run(
        ["git", "bundle", "create", "-", "--all", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise GitTransferError(f"git bundle failed: {result.stderr.decode()}")
    return result.stdout


def extract_bundle(bundle_bytes: bytes, dest_dir: str) -> None:
    tmp_file = tempfile.NamedTemporaryFile(suffix=".bundle", delete=False)
    try:
        tmp_file.write(bundle_bytes)
        tmp_file.flush()
        tmp_file.close()
        try:
            subprocess.run(
                ["git", "clone", tmp_file.name, dest_dir],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            raise GitTransferError(
                f"git clone from bundle failed: {exc.stderr.decode()}"
            ) from exc
    finally:
        Path(tmp_file.name).unlink(missing_ok=True)


def create_patch(repo_path: str) -> str:
    result = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout


def apply_patch(repo_path: str, patch: str) -> None:
    if patch == "":
        return
    tmp_file = tempfile.NamedTemporaryFile(
        suffix=".patch", delete=False, mode="w", encoding="utf-8"
    )
    try:
        tmp_file.write(patch)
        tmp_file.flush()
        tmp_file.close()
        result = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", tmp_file.name],
            cwd=repo_path,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise GitTransferError(f"git apply failed: {result.stderr.decode()}")
    finally:
        Path(tmp_file.name).unlink(missing_ok=True)
