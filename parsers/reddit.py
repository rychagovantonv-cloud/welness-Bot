"""Reddit-парсер через публичный .json эндпоинт. Без OAuth, без app.

Принимает любые формы URL:
- https://www.reddit.com/r/solotravel/comments/abc123/title/
- https://reddit.com/r/solotravel/comments/abc123/
- https://old.reddit.com/r/solotravel/comments/abc123/
"""

import re
from dataclasses import dataclass

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

USER_AGENT = "welness-bot/0.1 (audience research; contact rychagovantonv-cloud)"

REDDIT_URL_RE = re.compile(
    r"^https?://(?:www\.|old\.|new\.|np\.)?reddit\.com/r/([^/]+)/comments/([a-z0-9]+)",
    re.IGNORECASE,
)


@dataclass
class RedditComment:
    author: str
    body: str
    score: int
    depth: int


@dataclass
class RedditThread:
    subreddit: str
    thread_id: str
    title: str
    op_body: str
    op_score: int
    permalink: str
    comments: list[RedditComment]

    @property
    def url(self) -> str:
        return f"https://www.reddit.com{self.permalink}"

    @property
    def slug(self) -> str:
        return f"{self.subreddit}-{self.thread_id}"


def normalize_reddit_url(url: str) -> tuple[str, str] | None:
    """Возвращает (subreddit, thread_id) или None если не reddit-URL."""
    m = REDDIT_URL_RE.match(url.strip())
    if not m:
        return None
    return m.group(1), m.group(2).lower()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
async def _fetch_json(client: httpx.AsyncClient, url: str) -> list:
    r = await client.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


async def fetch_thread(
    url: str, *, top_n: int = 80, max_depth: int = 2
) -> RedditThread | None:
    """Грузит публичный Reddit-тред, возвращает up to top_n top комментов глубиной до max_depth."""
    parsed = normalize_reddit_url(url)
    if not parsed:
        logger.warning("not a reddit URL: {}", url[:120])
        return None
    subreddit, thread_id = parsed

    json_url = f"https://www.reddit.com/r/{subreddit}/comments/{thread_id}.json?sort=top&limit={top_n}"
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        try:
            data = await _fetch_json(client, json_url)
        except Exception as e:
            logger.error("reddit fetch failed", url=json_url, error=str(e))
            return None

    if not isinstance(data, list) or len(data) < 2:
        logger.warning("unexpected reddit response shape", subreddit=subreddit)
        return None

    op_listing = data[0].get("data", {}).get("children", [])
    if not op_listing:
        return None
    op = op_listing[0].get("data", {})

    comments_raw = data[1].get("data", {}).get("children", [])
    comments = _flatten_comments(comments_raw, max_depth=max_depth)
    # Sort by score desc и обрезаем
    comments.sort(key=lambda c: c.score, reverse=True)
    comments = comments[:top_n]

    return RedditThread(
        subreddit=subreddit,
        thread_id=thread_id,
        title=op.get("title", "")[:500],
        op_body=op.get("selftext", "")[:5000],
        op_score=int(op.get("score", 0)),
        permalink=op.get("permalink", f"/r/{subreddit}/comments/{thread_id}/"),
        comments=comments,
    )


def _flatten_comments(
    children: list[dict], depth: int = 0, max_depth: int = 2
) -> list[RedditComment]:
    if depth > max_depth:
        return []
    out: list[RedditComment] = []
    for c in children:
        kind = c.get("kind")
        if kind != "t1":  # t1 = comment, t3 = post, more = "load more" stub
            continue
        d = c.get("data", {})
        body = (d.get("body") or "").strip()
        if not body or body in ("[deleted]", "[removed]"):
            continue
        out.append(
            RedditComment(
                author=d.get("author") or "[unknown]",
                body=body[:2000],
                score=int(d.get("score", 0)),
                depth=depth,
            )
        )
        replies = d.get("replies")
        if isinstance(replies, dict):
            nested = replies.get("data", {}).get("children", [])
            out.extend(_flatten_comments(nested, depth=depth + 1, max_depth=max_depth))
    return out


def format_thread_for_llm(thread: RedditThread) -> str:
    parts = [
        f"# Subreddit: r/{thread.subreddit}",
        f"# Thread ID: {thread.thread_id}",
        f"# OP title: {thread.title}",
        f"# OP score: {thread.op_score}",
        "",
    ]
    if thread.op_body:
        parts.append(f"## OP body:\n{thread.op_body}\n")

    parts.append(f"## Top {len(thread.comments)} comments (sorted by score):\n")
    for i, c in enumerate(thread.comments, 1):
        indent = "  " * c.depth
        parts.append(f"{indent}[{i}] u/{c.author} (score={c.score}, depth={c.depth}):")
        parts.append(f"{indent}{c.body}\n")
    return "\n".join(parts)
