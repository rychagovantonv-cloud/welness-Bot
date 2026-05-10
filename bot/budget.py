"""Утилиты бюджет-строки для отображения в TG после операций с LLM."""

from decimal import Decimal

from config import settings
from database.client import session_scope
from database.repos import runs as runs_repo


def _budget_indicator(month_spent: Decimal) -> str:
    """Возвращает префикс-эмодзи в зависимости от % использования бюджета."""
    cap = settings.budget_monthly_usd
    if cap <= 0:
        return ""
    pct = month_spent / cap
    if pct >= 1:
        return "🚨 "
    if pct >= 0.8:
        return "⚠️ "
    if pct >= 0.5:
        return "🟡 "
    return ""


def format_budget_line(this_run_cost: Decimal | None, summary: dict[str, Decimal]) -> str:
    """Форматирует одну компактную строку: запуск + сегодня + месяц / лимит."""
    parts: list[str] = []
    if this_run_cost is not None:
        parts.append(f"запуск: ${this_run_cost:.4f}")
    parts.append(f"сегодня: ${summary['today']:.4f}")
    indicator = _budget_indicator(summary["month"])
    cap = settings.budget_monthly_usd
    parts.append(f"{indicator}месяц: ${summary['month']:.2f} / ${cap:.0f}")
    return "💰 " + "  ·  ".join(parts)


async def get_budget_line(this_run_cost: Decimal | None) -> str:
    """Шорткат: открывает сессию, получает summary, форматирует строку."""
    async with session_scope() as session:
        summary = await runs_repo.get_cost_summary(session)
    return format_budget_line(this_run_cost, summary)
