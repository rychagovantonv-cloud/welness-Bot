"""AEO engine: один запрос → ответы нескольких AI-моделей → мета-анализ.

Поддержанные модели:
- Claude (Anthropic) — всегда доступна (ключ обязателен).
- Gemini (Google AI Studio) — опционально, если задан GEMINI_API_KEY.

Если Gemini не настроен, AEO работает в single-model mode (Claude only)
и помечает это в результате.
"""

import asyncio
import json
from decimal import Decimal
from pathlib import Path

from anthropic import AsyncAnthropic
from loguru import logger
from pydantic import ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential

from ai_engine.client import _calculate_cost, get_client
from ai_engine.schemas import AeoAnalysis, AeoModelResponse
from config import settings

PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


SUBMIT_AEO_TOOL = {
    "name": "submit_aeo_analysis",
    "description": "Submit one AeoAnalysis comparing the model responses to the query.",
    "input_schema": AeoAnalysis.model_json_schema(),
}


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=2, max=10), reraise=True)
async def _ask_claude(query: str) -> tuple[str, Decimal]:
    client = get_client()
    system = _load_prompt("aeo_query")
    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": query}],
    )
    cost = _calculate_cost(response.usage.model_dump())
    text_blocks = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    return "\n".join(text_blocks).strip(), cost


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=2, max=10), reraise=True)
async def _ask_gemini(query: str) -> tuple[str, Decimal]:
    """Возвращает текст ответа и оценочную стоимость (на free tier — 0)."""
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")
    import google.generativeai as genai

    # genai.configure — sync, но безопасно дёргать многократно
    genai.configure(api_key=settings.gemini_api_key.get_secret_value())
    model = genai.GenerativeModel(settings.gemini_model)
    system = _load_prompt("aeo_query")

    def _sync_call() -> tuple[str, int, int]:
        resp = model.generate_content(
            f"{system}\n\nUser query: {query}",
            generation_config={"max_output_tokens": 1500, "temperature": 0.7},
        )
        text = (resp.text or "").strip()
        usage = getattr(resp, "usage_metadata", None)
        in_tok = getattr(usage, "prompt_token_count", 0) if usage else 0
        out_tok = getattr(usage, "candidates_token_count", 0) if usage else 0
        return text, in_tok, out_tok

    text, in_tok, out_tok = await asyncio.to_thread(_sync_call)
    # Gemini Flash оценочная цена: $0.075 / Mtok input, $0.30 / Mtok output (paid tier).
    # На free tier стоимость = 0; считаем по paid для консервативности.
    cost = (
        Decimal(in_tok) * Decimal("0.075") / 1_000_000
        + Decimal(out_tok) * Decimal("0.30") / 1_000_000
    ).quantize(Decimal("0.000001"))
    return text, cost


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=2, max=15), reraise=True)
async def _meta_analyze(
    query: str, responses: list[AeoModelResponse]
) -> tuple[AeoAnalysis | None, Decimal]:
    client = get_client()
    system = _load_prompt("aeo_analyst")

    user_payload = [f"# User query\n{query}\n", "# Model responses\n"]
    for r in responses:
        user_payload.append(f"## Model: {r.model}\n{r.raw_text}\n")
    user_text = "\n".join(user_payload)

    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=3000,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        tools=[SUBMIT_AEO_TOOL],
        tool_choice={"type": "tool", "name": "submit_aeo_analysis"},
        messages=[{"role": "user", "content": user_text}],
    )

    cost = _calculate_cost(response.usage.model_dump())
    tool_use_block = next(
        (b for b in response.content if getattr(b, "type", None) == "tool_use"), None
    )
    if tool_use_block is None:
        logger.error("aeo: no tool_use", stop_reason=response.stop_reason)
        return None, cost

    try:
        analysis = AeoAnalysis.model_validate(tool_use_block.input)
    except ValidationError as e:
        logger.error("aeo schema validation failed: {}", e)
        logger.error("raw: {}", json.dumps(tool_use_block.input)[:2000])
        return None, cost
    return analysis, cost


async def run_aeo(query: str) -> tuple[
    list[AeoModelResponse], AeoAnalysis | None, Decimal
]:
    """Параллельно опрашивает Claude и Gemini (если есть), затем мета-анализирует.

    Возвращает (raw_ответы_моделей, мета-анализ, общая_стоимость_USD).
    """
    tasks: list = [_ask_claude(query)]
    labels: list[str] = [settings.anthropic_model]

    has_gemini = bool(settings.gemini_api_key)
    if has_gemini:
        tasks.append(_ask_gemini(query))
        labels.append(settings.gemini_model)

    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    responses: list[AeoModelResponse] = []
    total_cost = Decimal(0)
    for label, res in zip(labels, raw_results, strict=False):
        if isinstance(res, Exception):
            logger.error("aeo model failed", model=label, error=str(res))
            continue
        text, cost = res
        responses.append(AeoModelResponse(model=label, raw_text=text))
        total_cost += cost

    if not responses:
        return [], None, total_cost

    analysis, meta_cost = await _meta_analyze(query, responses)
    total_cost += meta_cost
    return responses, analysis, total_cost
