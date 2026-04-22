"""LLM-as-judge: re-reads the agent's report and scores it for grounding.

Rationale: the verifier can hallucinate a source citation or back-fill a
value the tool never returned. The judge runs with a *different* system
prompt and (when available) a *different* deployment name so the two
don't collude on the same failure modes.

Input : ``outputs/report.json`` produced by ``snaq-verify verify``.
Output: ``outputs/judge.json`` with one :class:`JudgeVerdict` per item.

Run:
    uv run snaq-verify judge outputs/report.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from snaq_verify.config import Settings
from snaq_verify.models import JudgeVerdict

_LOG = logging.getLogger("snaq_verify.judge")

_JUDGE_SYSTEM_PROMPT = (
    "You are an evaluator. Read one food item's automated verification "
    "result and decide whether the stated reasoning is actually supported "
    "by the listed sources and tool trace. Flag: (a) claims not grounded "
    "in any source, (b) arithmetic the agent did itself rather than via a "
    "tool, (c) proposed correction values that don't match any source. "
    "Return a JudgeVerdict. Be strict: if the reasoning cites a number "
    "that isn't in any source, it is not grounded."
)


def _build_judge_agent(settings: Settings) -> Agent[None, JudgeVerdict]:
    """Build the judge agent. Uses a separate deployment when configured."""
    deployment = os.environ.get("AZURE_OPENAI_JUDGE_DEPLOYMENT") or settings.azure_deployment
    base_url = settings.azure_endpoint.rstrip("/") + "/"
    client = AsyncOpenAI(base_url=base_url, api_key=settings.azure_api_key)
    model = OpenAIChatModel(deployment, provider=OpenAIProvider(openai_client=client))
    return Agent(model=model, output_type=JudgeVerdict, system_prompt=_JUDGE_SYSTEM_PROMPT)


async def _judge_one(agent: Agent[None, JudgeVerdict], row: dict) -> JudgeVerdict:
    """Send one report row to the judge."""
    prompt = (
        "Evaluate this verification result.\n\n"
        f"ITEM:\n{json.dumps(row['item'], indent=2)}\n\n"
        f"RESULT:\n{json.dumps(row['result'], indent=2)}\n\n"
        f"TRACE:\n{json.dumps(row['trace'], indent=2)}\n\n"
        f"Item id is {row['result']['item_id']!r}. "
        "Set JudgeVerdict.item_id to that exact string."
    )
    run = await agent.run(prompt)
    verdict = run.output
    # Defend against the judge picking a different id.
    if verdict.item_id != row["result"]["item_id"]:
        verdict = verdict.model_copy(update={"item_id": row["result"]["item_id"]})
    return verdict


async def run_judge(report_path: Path, out_path: Path, concurrency: int = 3) -> None:
    """Score every item in ``report_path`` and write ``out_path``."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    for name in ("httpx", "httpcore", "openai", "pydantic_ai"):
        logging.getLogger(name).setLevel(logging.WARNING)

    settings = Settings.load()
    doc = json.loads(report_path.read_text())
    rows = doc.get("items", [])
    _LOG.info("Judging %d items", len(rows))

    agent = _build_judge_agent(settings)
    sem = asyncio.Semaphore(concurrency)

    async def one(row: dict) -> JudgeVerdict:
        async with sem:
            try:
                v = await _judge_one(agent, row)
            except Exception as exc:
                _LOG.exception("Judge failed for %s", row["result"]["item_id"])
                v = JudgeVerdict(
                    item_id=row["result"]["item_id"],
                    grounded=False,
                    concerns=[f"judge raised: {type(exc).__name__}: {exc}"],
                    judge_confidence=0.0,
                    summary="Judge failed to produce a verdict.",
                )
            _LOG.info(
                "[judge] %-28s grounded=%s conf=%.2f %s",
                v.item_id,
                v.grounded,
                v.judge_confidence,
                f"({len(v.concerns)} concerns)" if v.concerns else "",
            )
            return v

    verdicts = await asyncio.gather(*(one(r) for r in rows))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "report_path": str(report_path),
                "verdicts": [v.model_dump() for v in verdicts],
            },
            indent=2,
            default=str,
        )
    )
    n_grounded = sum(1 for v in verdicts if v.grounded)
    _LOG.info(
        "Judge done: %d/%d grounded -> %s",
        n_grounded,
        len(verdicts),
        out_path,
    )
