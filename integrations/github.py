"""GitHub integration: commit approved card as a Markdown file in content repo."""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from github import Auth, Github, GithubException
from github.Repository import Repository
from loguru import logger

from ai_engine.schemas import RadarCard
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
