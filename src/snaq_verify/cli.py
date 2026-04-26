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
    concurrency: Annotated[
        int | None,
        typer.Option("--concurrency", "-c", help="Override MAX_CONCURRENT_VERIFICATIONS."),
    ] = None,
    reasoning_effort: Annotated[
        str | None,
        typer.Option(
            "--reasoning-effort",
            "-r",
            help=(
                "Forwarded to OpenAI-family reasoning models "
                "(minimal|low|medium|high). Higher = more deliberation, "
                "higher cost. Defaults to the deployment's default."
            ),
        ),
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

    valid_efforts = {"minimal", "low", "medium", "high"}
    if reasoning_effort is not None and reasoning_effort not in valid_efforts:
        raise typer.BadParameter(
            f"unknown reasoning effort {reasoning_effort!r}. "
            f"Valid: {sorted(valid_efforts)}"
        )

    requested_formats = tuple(f.strip() for f in formats.split(",") if f.strip())
    asyncio.run(
        run_verification(
            input_file=input_file,
            out_dir=out,
            formats=requested_formats,
            concurrency_override=concurrency,
            verbose=verbose,
            reasoning_effort=reasoning_effort,
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


@app.command()
def stability(
    input_file: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Path to food_items.json.",
        ),
    ],
    runs: Annotated[
        int,
        typer.Option(
            "--runs",
            "-k",
            min=1,
            help="Number of independent verify (and judge) runs PER effort level.",
        ),
    ] = 3,
    efforts: Annotated[
        str,
        typer.Option(
            "--efforts",
            help="Comma-separated reasoning effort levels to sweep "
            "(any of: minimal, low, medium, high).",
        ),
    ] = "minimal,low,medium,high",
    out: Annotated[
        Path,
        typer.Option(
            "--out",
            "-o",
            help="Output directory (a 'stability/' subdir is created inside).",
        ),
    ] = Path("outputs"),
    no_judge: Annotated[
        bool,
        typer.Option(
            "--no-judge",
            help="Skip running the LLM-as-judge after each verify run.",
        ),
    ] = False,
    concurrency: Annotated[
        int | None,
        typer.Option("--concurrency", "-c", help="Override MAX_CONCURRENT_VERIFICATIONS."),
    ] = None,
    verbose: Annotated[
        int,
        typer.Option("--verbose", "-v", count=True, help="Increase log verbosity."),
    ] = 0,
) -> None:
    """Sweep reasoning effort x K runs; aggregate a stability matrix.

    At n=11 with no hand-labelled ground truth, stability (the agent
    agreeing with itself across runs) is a more honest signal than a
    fabricated golden set. Sweeping ``reasoning_effort`` lets us see
    whether higher effort actually buys more grounded answers, or just
    burns tokens. Writes ``<out>/stability/<effort>/run_{k}/`` for each
    (effort, run) pair plus ``<out>/stability/matrix.{json,md}``.
    """
    import asyncio

    from eval.stability import DEFAULT_EFFORTS, run_stability

    valid = set(DEFAULT_EFFORTS)
    parsed_efforts = tuple(e.strip() for e in efforts.split(",") if e.strip())
    invalid = [e for e in parsed_efforts if e not in valid]
    if invalid:
        raise typer.BadParameter(
            f"unknown effort(s): {invalid}. Valid: {sorted(valid)}"
        )
    if not parsed_efforts:
        raise typer.BadParameter("at least one effort level is required")

    asyncio.run(
        run_stability(
            input_file=input_file,
            runs=runs,
            out_dir=out,
            efforts=parsed_efforts,
            include_judge=not no_judge,
            concurrency_override=concurrency,
            verbose=verbose,
        )
    )


if __name__ == "__main__":
    app()
