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


def test_verify_help_exposes_reasoning_effort_flag() -> None:
    result = CliRunner().invoke(app, ["verify", "--help"])
    assert result.exit_code == 0
    assert "--reasoning-effort" in result.stdout


def test_verify_rejects_unknown_reasoning_effort(tmp_path) -> None:
    food = tmp_path / "food_items.json"
    food.write_text("[]")
    result = CliRunner().invoke(
        app,
        ["verify", str(food), "--reasoning-effort", "ludicrous"],
    )
    assert result.exit_code != 0
    assert "ludicrous" in result.stdout or "ludicrous" in (result.stderr or "")
