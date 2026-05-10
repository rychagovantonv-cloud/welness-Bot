"""Anthropic SDK wrapper: structured output via tool_use + prompt caching."""

import json
from decimal import Decimal
from pathlib import Path

from anthropic import AsyncAnthropic
from loguru import logger
from pydantic import ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential

from ai_engine.schemas import InsightReport, RadarBatchResult, RadarCard
from config import settings
from parsers.base import RawItem

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Анропик прайсинг для Haiku 4.5 на момент 2026-05 (USD/Mtok).
# Используется только для оценки в run_logs, не критично если устареет.
PRICE_INPUT_PER_MTOK = Decimal("1.00")
PRICE_OUTPUT_PER_MTOK = Decimal("5.00")
PRICE_CACHE_WRITE_PER_MTOK = Decimal("1.25")
PRICE_CACHE_READ_PER_MTOK = Decimal("0.10")

_client: AsyncAnthropic | None = None


def get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
    return _client


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


SUBMIT_CARDS_TOOL = {
    "name": "submit_radar_cards",
    "description": (
        "Submit one card per input item. Items must be in the same order as the input. "
        "Trash items get is_trash=true with a one-line reason in summary."
    ),
    "input_schema": RadarBatchResult.model_json_schema(),
}


def _format_batch(items: list[RawItem]) -> str:
    parts: list[str] = []
    for i, item in enumerate(items, 1):
        body = item.body[:4000] if item.body else "(no body)"
        parts.append(
            f"=== ITEM {i} ===\n"
            f"external_id: {item.external_id}\n"
            f"source: {item.source}\n"
            f"url: {item.url}\n"
            f"title: {item.title}\n"
            f"body:\n{body}"
        )
    return "\n\n".join(parts)


def _calculate_cost(usage: dict) -> Decimal:
    input_tok = Decimal(usage.get("input_tokens", 0))
    output_tok = Decimal(usage.get("output_tokens", 0))
    cache_read = Decimal(usage.get("cache_read_input_tokens", 0))
    cache_write = Decimal(usage.get("cache_creation_input_tokens", 0))
    cost = (
        input_tok * PRICE_INPUT_PER_MTOK / 1_000_000
        + output_tok * PRICE_OUTPUT_PER_MTOK / 1_000_000
        + cache_write * PRICE_CACHE_WRITE_PER_MTOK / 1_000_000
        + cache_read * PRICE_CACHE_READ_PER_MTOK / 1_000_000
    )
    return cost.quantize(Decimal("0.000001"))


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15), reraise=True)
async def summarize_radar_batch(items: list[RawItem]) -> tuple[list[RadarCard], Decimal]:
    """Прогоняет батч RawItem через LLM, возвращает карточки + стоимость в USD.

    Системный промпт помечен cache_control=ephemeral — на повторных вызовах
    пойдёт по cache_read цене (~10× дешевле).
    """
    if not items:
        return [], Decimal(0)

    client = get_client()
    system_text = _load_prompt("radar_summary")
    user_text = _format_batch(items)

    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=4096,
        system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
        tools=[SUBMIT_CARDS_TOOL],
        tool_choice={"type": "tool", "name": "submit_radar_cards"},
        messages=[{"role": "user", "content": user_text}],
    )

    cost = _calculate_cost(response.usage.model_dump())
    logger.info(
        "anthropic call",
        items=len(items),
        input_tok=response.usage.input_tokens,
        output_tok=response.usage.output_tokens,
        cache_read=getattr(response.usage, "cache_read_input_tokens", 0),
        cache_write=getattr(response.usage, "cache_creation_input_tokens", 0),
        cost_usd=str(cost),
    )

    tool_use_block = next(
        (b for b in response.content if getattr(b, "type", None) == "tool_use"), None
    )
    if tool_use_block is None:
        logger.error("no tool_use block in response", stop_reason=response.stop_reason)
        return [], cost

    try:
        result = RadarBatchResult.model_validate(tool_use_block.input)
    except ValidationError as e:
        logger.error("LLM output failed schema validation: {}", e)
        # Лог сырого вывода для отладки
        logger.error("raw tool input: {}", json.dumps(tool_use_block.input)[:2000])
        return [], cost

    return result.cards, cost


SUBMIT_INSIGHT_TOOL = {
    "name": "submit_insight_report",
    "description": "Submit one structured InsightReport for the analyzed thread.",
    "input_schema": InsightReport.model_json_schema(),
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15), reraise=True)
async def analyze_insight(thread_text: str) -> tuple[InsightReport | None, Decimal]:
    """Прогоняет уже отформатированный тред через analyst-промпт.

    Возвращает (отчёт, стоимость USD) либо (None, стоимость) если LLM не сумел.
    """
    client = get_client()
    system_text = _load_prompt("insight_analyst")

    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=4096,
        system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
        tools=[SUBMIT_INSIGHT_TOOL],
        tool_choice={"type": "tool", "name": "submit_insight_report"},
        messages=[{"role": "user", "content": thread_text}],
    )

    cost = _calculate_cost(response.usage.model_dump())
    logger.info(
        "anthropic insight call",
        input_tok=response.usage.input_tokens,
        output_tok=response.usage.output_tokens,
        cost_usd=str(cost),
    )

    tool_use_block = next(
        (b for b in response.content if getattr(b, "type", None) == "tool_use"), None
    )
    if tool_use_block is None:
        logger.error("no tool_use in insight response", stop_reason=response.stop_reason)
        return None, cost

    try:
        report = InsightReport.model_validate(tool_use_block.input)
    except ValidationError as e:
        logger.error("insight schema validation failed: {}", e)
        logger.error("raw tool input: {}", json.dumps(tool_use_block.input)[:2000])
        return None, cost

    return report, cost
