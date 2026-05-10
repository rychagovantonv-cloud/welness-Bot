"""GitHub integration: commit approved card as a Markdown file in content repo."""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from github import Auth, Github, GithubException
from github.Repository import Repository
from loguru import logger

from ai_engine.schemas import AeoAnalysis, AeoModelResponse, InsightReport, RadarCard
from config import settings


@dataclass(frozen=True)
class CommitResult:
    sha: str
    path: str
    html_url: str


_repo: Repository | None = None


def _get_repo() -> Repository:
    global _repo
    if _repo is None:
        if not settings.github_token or not settings.github_content_repo:
            raise RuntimeError("GITHUB_TOKEN or GITHUB_CONTENT_REPO not configured")
        gh = Github(auth=Auth.Token(settings.github_token.get_secret_value()))
        _repo = gh.get_repo(settings.github_content_repo)
    return _repo


def _slugify(text: str, max_len: int = 60) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text).strip("-")
    return text[:max_len].rstrip("-") or "untitled"


def _frontmatter(card: RadarCard, original_title: str, *, approved_by: int) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        "---\n"
        f"title: {_yaml_str(card.title)}\n"
        f"original_title: {_yaml_str(original_title)}\n"
        f"source: {card.source}\n"
        f"source_url: {card.url}\n"
        f"transformation_type: {card.transformation_type}\n"
        f"relevance: {card.relevance}\n"
        f"approved_at: {today}\n"
        f"approved_by: {approved_by}\n"
        "---\n"
    )


def _yaml_str(s: str) -> str:
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _build_markdown(card: RadarCard, original_title: str, *, approved_by: int) -> str:
    return (
        _frontmatter(card, original_title, approved_by=approved_by)
        + "\n"
        + f"# {card.title}\n\n"
        + f"_{original_title}_\n\n"
        + f"**Источник:** [{card.source}]({card.url})\n\n"
        + f"## Инсайт\n\n{card.summary}\n"
    )


def _build_path(card: RadarCard, original_title: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Slug from English original to keep file paths ASCII-clean.
    # Если original пуст или весь не-ASCII — fall back на slug из card.title.
    slug = _slugify(original_title)
    if slug == "untitled":
        slug = _slugify(card.title)
    return f"{card.transformation_type}/{today}-{slug}.md"


def _commit_sync(card: RadarCard, original_title: str, *, approved_by: int) -> CommitResult:
    repo = _get_repo()
    path = _build_path(card, original_title)
    content = _build_markdown(card, original_title, approved_by=approved_by)
    message = f"Add: {original_title[:80] if original_title else card.title[:80]}"

    try:
        result = repo.create_file(path, message, content, branch="main")
    except GithubException as e:
        # Если файл уже существует — добавляем суффикс с timestamp и пробуем снова
        if e.status == 422:
            ts = datetime.now(timezone.utc).strftime("%H%M%S")
            base, ext = path.rsplit(".", 1)
            path = f"{base}-{ts}.{ext}"
            result = repo.create_file(path, message, content, branch="main")
        else:
            raise

    commit = result["commit"]
    return CommitResult(sha=commit.sha, path=path, html_url=commit.html_url)


async def commit_approved(
    card: RadarCard, *, original_title: str, approved_by: int
) -> CommitResult:
    """Async-обёртка над PyGithub (sync API), уходит в thread pool."""
    try:
        return await asyncio.to_thread(
            _commit_sync, card, original_title, approved_by=approved_by
        )
    except Exception as e:
        logger.error("github commit failed", error=str(e), title=card.title[:60])
        raise


# ---- Insight (Phase 2) ----


def _build_insight_markdown(
    report: InsightReport, source_url: str, source_label: str
) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fm = (
        "---\n"
        f"source_label: {_yaml_str(source_label)}\n"
        f"source_url: {source_url}\n"
        f"audience_segment: {_yaml_str(report.audience_segment)}\n"
        f"pain_points_count: {len(report.pain_points)}\n"
        f"approved_at: {today}\n"
        "---\n\n"
    )
    body: list[str] = [f"# Insight: {source_label}\n"]
    body.append(f"**Источник:** [{source_url}]({source_url})\n")
    body.append(f"**Сегмент:** {report.audience_segment}\n")

    body.append("## Боли\n")
    for i, pp in enumerate(report.pain_points, 1):
        body.append(f"### {i}. {pp.title} _({pp.category}, freq={pp.frequency})_")
        body.append(pp.description)
        body.append("")
        for q in pp.representative_quotes:
            body.append(f"> {q}")
        body.append("")

    body.append("## Желания\n")
    for d in report.desires:
        body.append(f"- {d}")
    body.append("")

    body.append("## Триггеры\n")
    for t in report.triggers:
        body.append(f"- {t}")
    body.append("")

    body.append("## AEO-конспект\n")
    body.append(report.summary_for_aeo)
    body.append("")

    return fm + "\n".join(body)


def _commit_insight_sync(
    report: InsightReport, *, source_url: str, source_label: str, slug: str
) -> CommitResult:
    repo = _get_repo()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    safe_slug = _slugify(slug) or "thread"
    path = f"insights/{today}-{safe_slug}.md"
    content = _build_insight_markdown(report, source_url, source_label)
    message = f"Insight: {source_label[:80]}"
    try:
        result = repo.create_file(path, message, content, branch="main")
    except GithubException as e:
        if e.status == 422:
            ts = datetime.now(timezone.utc).strftime("%H%M%S")
            base, ext = path.rsplit(".", 1)
            path = f"{base}-{ts}.{ext}"
            result = repo.create_file(path, message, content, branch="main")
        else:
            raise
    commit = result["commit"]
    return CommitResult(sha=commit.sha, path=path, html_url=commit.html_url)


async def commit_insight(
    report: InsightReport, *, source_url: str, source_label: str, slug: str
) -> CommitResult:
    try:
        return await asyncio.to_thread(
            _commit_insight_sync,
            report,
            source_url=source_url,
            source_label=source_label,
            slug=slug,
        )
    except Exception as e:
        logger.error("github insight commit failed", error=str(e), label=source_label[:60])
        raise


# ---- AEO ----


def _build_aeo_markdown(
    query: str,
    responses: list[AeoModelResponse],
    analysis: AeoAnalysis,
) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fm = (
        "---\n"
        f"query: {_yaml_str(query)}\n"
        f"models: {[r.model for r in responses]}\n"
        f"saved_at: {today}\n"
        "---\n\n"
    )
    parts: list[str] = [
        f"# AEO: {query}\n",
        f"**Запрос:** _{query}_\n",
        f"**Моделей опрошено:** {len(responses)}\n",
        "## Доминирующий нарратив\n",
        analysis.dominant_narrative,
        "",
        "## Общие темы\n",
    ]
    for t in analysis.common_themes:
        parts.append(f"- {t}")
    parts.append("")

    parts.append("## Уникальные углы по моделям\n")
    for model, items in analysis.unique_angles.items():
        parts.append(f"### {model}")
        for it in items:
            parts.append(f"- {it}")
        parts.append("")

    parts.append("## Контентные пробелы (где можно вклиниться)\n")
    for g in analysis.content_gaps:
        parts.append(f"- {g}")
    parts.append("")

    parts.append("## Рекомендованные ключи (AEO)\n")
    for k in analysis.recommended_keywords:
        parts.append(f"- `{k}`")
    parts.append("")

    parts.append("## Что делать\n")
    parts.append(analysis.summary)
    parts.append("")

    parts.append("---\n")
    parts.append("## Сырые ответы моделей\n")
    for r in responses:
        parts.append(f"### {r.model}\n")
        parts.append(r.raw_text)
        parts.append("")

    return fm + "\n".join(parts)


def _commit_aeo_sync(
    query: str,
    responses: list[AeoModelResponse],
    analysis: AeoAnalysis,
    *,
    slug: str,
) -> CommitResult:
    repo = _get_repo()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    safe_slug = _slugify(slug) or "query"
    path = f"aeo/{today}-{safe_slug}.md"
    content = _build_aeo_markdown(query, responses, analysis)
    message = f"AEO: {query[:80]}"
    try:
        result = repo.create_file(path, message, content, branch="main")
    except GithubException as e:
        if e.status == 422:
            ts = datetime.now(timezone.utc).strftime("%H%M%S")
            base, ext = path.rsplit(".", 1)
            path = f"{base}-{ts}.{ext}"
            result = repo.create_file(path, message, content, branch="main")
        else:
            raise
    commit = result["commit"]
    return CommitResult(sha=commit.sha, path=path, html_url=commit.html_url)


async def commit_aeo(
    query: str,
    responses: list[AeoModelResponse],
    analysis: AeoAnalysis,
    *,
    slug: str,
) -> CommitResult:
    try:
        return await asyncio.to_thread(
            _commit_aeo_sync, query, responses, analysis, slug=slug
        )
    except Exception as e:
        logger.error("github aeo commit failed", error=str(e), query=query[:60])
        raise
