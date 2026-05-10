"""Стоимость запуска + остаток баланса Anthropic-кабинета.

Anthropic не отдаёт реальный остаток через публичный API. Поэтому работаем
по снимку: вы один раз указываете в env свой текущий баланс + timestamp,
бот вычитает все расходы которые были ПОСЛЕ этого момента.
"""

from decimal import Decimal

from config import settings
from database.client import session_scope
from database.repos import runs as runs_repo


def _balance_indicator(balance: Decimal, initial: Decimal) -> str:
    """⚠️/🚨 если остаток мал относительно начального снимка."""
    if initial <= 0:
        return ""
    pct = balance / initial
    if pct <= 0:
        return "🚨 "
    if pct <= 0.2:
        return "⚠️ "
    return ""


def format_budget_line(this_run_cost: Decimal | None, spent_since_snapshot: Decimal) -> str:
    """Одна строка: запуск + остаток (если снимок задан)."""
    parts: list[str] = []
    if this_run_cost is not None:
        parts.append(f"запуск: ${this_run_cost:.4f}")
    if settings.anthropic_balance_usd is not None:
        initial = settings.anthropic_balance_usd
        balance = initial - spent_since_snapshot
        ind = _balance_indicator(balance, initial)
        parts.append(f"{ind}остаток: ${balance:.2f}")
    if not parts:
        return ""
    return "💰 " + "  ·  ".join(parts)


async def get_budget_line(this_run_cost: Decimal | None) -> str:
    async with session_scope() as session:
        spent = await runs_repo.get_spent_since(session, settings.anthropic_balance_as_of)
    return format_budget_line(this_run_cost, spent)
