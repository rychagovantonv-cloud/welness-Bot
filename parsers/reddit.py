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


@dataclass
class RedditSearchHit:
    title: str
    subreddit: str
    thread_id: str
    permalink: str
    num_comments: int
    score: int
    created_utc: float
    snippet: str

    @property
    def url(self) -> str:
        return f"https://www.reddit.com{self.permalink}"


# Сабреддиты с релевантным голосом для Reflective Traveler.
# Можно расширять — комментариями/PR.
CURATED_SUBREDDITS: tuple[str, ...] = (
    "solotravel",
    "digitalnomad",
    "femaletravels",
    "Mindfulness",
    "Buddhism",
    "Meditation",
    "midlife",
    "decidingtobebetter",
    "selfimprovement",
    "Psychonaut",
    "rationaldharma",
    "transformative_travel",
    "AskWomenOver30",
    "AskMenOver30",
    "burnout",
    "ExistentialMemes",
    "TheWayWeWere",
    "spirituality",
)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
async def _search_endpoint(
    client: httpx.AsyncClient, *, query: str, subreddit: str | None,
    time_range: str, sort: str, limit: int,
) -> list[dict]:
    base = "https://www.reddit.com"
    if subreddit:
        url = f"{base}/r/{subreddit}/search.json"
        params = {"q": query, "restrict_sr": "1", "sort": sort, "t": time_range, "limit": limit}
    else:
        url = f"{base}/search.json"
        params = {"q": query, "sort": sort, "t": time_range, "limit": limit}
    r = await client.get(url, params=params, timeout=20)
    r.raise_for_status()
    children = r.json().get("data", {}).get("children", [])
    return [c.get("data", {}) for c in children if c.get("kind") == "t3"]


async def search_threads(
    query: str,
    *,
    subreddit: str | None = None,
    time_range: str = "month",
    sort: str = "top",
    limit: int = 25,
    min_comments: int = 20,
    min_score: int = 10,
    use_curated: bool = True,
) -> list[RedditSearchHit]:
    """Ищет треды по запросу.

    Если subreddit задан — ищет внутри него.
    Иначе если use_curated — параллельно опрашивает CURATED_SUBREDDITS, агрегирует.
    Иначе — глобальный поиск по reddit.com.
    """
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        all_hits: dict[str, dict] = {}  # dedup by thread id
        try:
            if subreddit:
                rows = await _search_endpoint(
                    client, query=query, subreddit=subreddit,
                    time_range=time_range, sort=sort, limit=limit,
                )
                for d in rows:
                    all_hits[d.get("id", "")] = d
            elif use_curated:
                # Параллельный fan-out по куратору, ограниченный таймаутом.
                import asyncio as _aio
                tasks = [
                    _search_endpoint(
                        client, query=query, subreddit=sr,
                        time_range=time_range, sort=sort, limit=10,
                    )
                    for sr in CURATED_SUBREDDITS
                ]
                results = await _aio.gather(*tasks, return_exceptions=True)
                for res in results:
                    if isinstance(res, Exception):
                        continue
                    for d in res:
                        all_hits[d.get("id", "")] = d
            else:
                rows = await _search_endpoint(
                    client, query=query, subreddit=None,
                    time_range=time_range, sort=sort, limit=limit,
                )
                for d in rows:
                    all_hits[d.get("id", "")] = d
        except Exception as e:
            logger.error("reddit search failed", query=query[:80], error=str(e))
            return []

    hits: list[RedditSearchHit] = []
    for d in all_hits.values():
        nc = int(d.get("num_comments", 0))
        sc = int(d.get("score", 0))
        if nc < min_comments or sc < min_score:
            continue
        hits.append(
            RedditSearchHit(
                title=d.get("title", "")[:300],
                subreddit=d.get("subreddit", ""),
                thread_id=d.get("id", ""),
                permalink=d.get("permalink", ""),
                num_comments=nc,
                score=sc,
                created_utc=float(d.get("created_utc", 0)),
                snippet=(d.get("selftext", "") or "")[:200],
            )
        )
    # Sort by score desc
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits


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
