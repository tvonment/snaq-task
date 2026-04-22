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
        typer.Option(
            "--format",
            "-f",
            help="Comma-separated report formats (json, md).",
        ),
    ] = "json,md",
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
    concurrency: Annotated[
        int | None,
        typer.Option("--concurrency", "-c", help="Override MAX_CONCURRENT_VERIFICATIONS."),
    ] = None,
    verbose: Annotated[
        int,
        typer.Option(
            "--verbose",
            "-v",
            count=True,
            help="Increase log verbosity. -v enables DEBUG for snaq_verify; "
            "-vv also re-enables httpx/openai/pydantic-ai INFO logging.",
        ),
    ] = 0,
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
            concurrency_override=concurrency,
            verbose=verbose,
        )
    )


@app.command()
def judge(
    report: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Path to report.json produced by 'verify'.",
        ),
    ],
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Output path for judge.json."),
    ] = Path("outputs/judge.json"),
    concurrency: Annotated[
        int,
        typer.Option("--concurrency", "-c", help="Judge request concurrency."),
    ] = 3,
) -> None:
    """Score an existing report for grounding via a second LLM (LLM-as-judge).

    Reads the JSON report written by ``verify`` and writes a
    :class:`JudgeVerdict` per item. Set ``AZURE_OPENAI_JUDGE_DEPLOYMENT``
    to route the judge to a different deployment than the verifier.
    """
    import asyncio

    from eval.judge import run_judge

    asyncio.run(run_judge(report_path=report, out_path=out, concurrency=concurrency))


if __name__ == "__main__":
    app()
