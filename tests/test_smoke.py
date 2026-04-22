"""Smoke test: package imports cleanly and the CLI exposes --help."""

from __future__ import annotations

from typer.testing import CliRunner

from snaq_verify import __version__
from snaq_verify.cli import app


def test_package_has_version() -> None:
    assert __version__ == "0.1.0"


def test_cli_help_renders() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "food_items.json" in result.stdout
