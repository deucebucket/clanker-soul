"""Smoke tests for the examples/ directory.

Each example is run as a subprocess. We don't assert on output — we just
require exit code 0. If an example breaks (API drift, removed kwarg,
forgotten import), CI catches it here before it reaches a confused
adopter trying to copy from the docs.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def _examples() -> list[Path]:
    return sorted(EXAMPLES_DIR.glob("[0-9][0-9]_*.py"))


@pytest.mark.parametrize("script", _examples(), ids=lambda p: p.name)
def test_example_runs(script: Path) -> None:
    """Run each example script as a subprocess; exit must be 0."""
    pytest.importorskip("fastapi")  # examples don't need it but [ui] tests imply it
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"{script.name} exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_examples_dir_has_at_least_four_scripts() -> None:
    """Cheap canary so a renamed/deleted example fails loud."""
    assert len(_examples()) >= 4
