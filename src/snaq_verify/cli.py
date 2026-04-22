"""Typer CLI entrypoint.

The real work lives in :mod:`snaq_verify.runner`. This module is intentionally
thin -- it only parses arguments and wires configuration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    name="snaq-verify",
    help="Verify nutrition data in food_items.json against authoritative sources.",
    add_completion=False,
    no_args_is_help=True,
)


@app.command()
def verify(
    input_file: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Path to food_items.json.",
        ),
    ],
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Output directory for report files."),
    ] = Path("outputs"),
    formats: Annotated[
        str,
        typer.Option("--format", "-f", help="Comma-separated report formats."),
    ] = "json,md,html",
    apply_corrections: Annotated[
        bool,
        typer.Option(
            "--apply-corrections",
            help="Emit food_items.corrected.json with accepted corrections merged in.",
        ),
    ] = False,
    min_confidence: Annotated[
        float,
        typer.Option(
            "--min-confidence",
            help="Minimum confidence required to auto-apply a correction.",
        ),
    ] = 0.8,
    no_cache: Annotated[
        bool,
        typer.Option("--no-cache", help="Bypass the on-disk response cache."),
    ] = False,
    concurrency: Annotated[
        int | None,
        typer.Option("--concurrency", "-c", help="Override MAX_CONCURRENT_VERIFICATIONS."),
    ] = None,
) -> None:
    """Verify each item in ``input_file`` and write a report to ``out``."""
    # Imported lazily so --help works without env vars / optional deps.
    import asyncio

    from snaq_verify.runner import run_verification

    requested_formats = tuple(f.strip() for f in formats.split(",") if f.strip())
    asyncio.run(
        run_verification(
            input_file=input_file,
            out_dir=out,
            formats=requested_formats,
            apply_corrections=apply_corrections,
            min_confidence=min_confidence,
            use_cache=not no_cache,
            concurrency_override=concurrency,
        )
    )


if __name__ == "__main__":
    app()
