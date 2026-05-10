"""Reddit-парсер через OAuth (script app, application-only flow).

Anonymous .json эндпоинт с 2023 заблокирован Reddit'ом для всех cloud IP.
Поэтому используем application-only OAuth: client_credentials grant даёт
Bearer-токен на 1 час, после чего обновляем.

Если REDDIT_CLIENT_ID/SECRET не заданы — fallback на www.reddit.com/.json
(работает только локально, в продакшене 403).

Принимает любые формы URL треда:
- https://www.reddit.com/r/solotravel/comments/abc123/title/
- https://reddit.com/r/solotravel/comments/abc123/
- https://old.reddit.com/r/solotravel/comments/abc123/
"""

import asyncio
import re
import time
from dataclasses import dataclass

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings

# User-Agent теперь берётся из settings, см. _http_headers().

# OAuth state (in-memory, refresh on expiry)
_oauth_token: str | None = None
_oauth_expires_at: float = 0.0
_oauth_lock = asyncio.Lock()

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


def _has_oauth() -> bool:
    return bool(settings.reddit_client_id and settings.reddit_client_secret)


async def _get_oauth_token() -> str | None:
    """Возвращает свежий Bearer-токен или None если OAuth не настроен."""
    global _oauth_token, _oauth_expires_at
    if not _has_oauth():
        return None

    # Запас 60с до реального истечения чтобы не словить просроченный токен в полёте
    if _oauth_token and time.time() < _oauth_expires_at - 60:
        return _oauth_token

    async with _oauth_lock:
        if _oauth_token and time.time() < _oauth_expires_at - 60:
            return _oauth_token

        client_id = settings.reddit_client_id.get_secret_value()
        client_secret = settings.reddit_client_secret.get_secret_value()

        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(client_id, client_secret),
                headers={"User-Agent": settings.reddit_user_agent},
                data={"grant_type": "client_credentials"},
            )
            r.raise_for_status()
            payload = r.json()

        _oauth_token = payload["access_token"]
        ttl = int(payload.get("expires_in", 3600))
        _oauth_expires_at = time.time() + ttl
        logger.info("reddit oauth refreshed", ttl_sec=ttl)
        return _oauth_token


async def _http_headers() -> dict[str, str]:
    headers = {"User-Agent": settings.reddit_user_agent}
    token = await _get_oauth_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _api_base() -> str:
    """Если есть OAuth — используем oauth.reddit.com, иначе www.reddit.com."""
    return "https://oauth.reddit.com" if _has_oauth() else "https://www.reddit.com"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
async def _fetch_json(client: httpx.AsyncClient, url: str) -> list:
    headers = await _http_headers()
    r = await client.get(url, headers=headers, timeout=20)
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

    base = _api_base()
    # OAuth endpoint без .json suffix; на www.reddit.com нужен .json
    if base.endswith("oauth.reddit.com"):
        json_url = f"{base}/r/{subreddit}/comments/{thread_id}?sort=top&limit={top_n}&raw_json=1"
    else:
        json_url = f"{base}/r/{subreddit}/comments/{thread_id}.json?sort=top&limit={top_n}"
    async with httpx.AsyncClient(follow_redirects=True) as client:
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
    base = _api_base()
    suffix = "" if base.endswith("oauth.reddit.com") else ".json"
    if subreddit:
        url = f"{base}/r/{subreddit}/search{suffix}"
        params = {
            "q": query, "restrict_sr": "1", "sort": sort,
            "t": time_range, "limit": limit, "raw_json": 1,
        }
    else:
        url = f"{base}/search{suffix}"
        params = {"q": query, "sort": sort, "t": time_range, "limit": limit, "raw_json": 1}
    headers = await _http_headers()
    r = await client.get(url, params=params, headers=headers, timeout=20)
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
    min_comments: int = 10,
    min_score: int = 5,
    use_curated: bool = True,
) -> tuple[list[RedditSearchHit], dict[str, int]]:
    """Ищет треды по запросу. Возвращает (hits, diag) где diag содержит
    счётчики для отладки: subs_ok, subs_failed, raw_total, after_filter.

    Если subreddit задан — ищет внутри него.
    Иначе если use_curated — параллельно опрашивает CURATED_SUBREDDITS,
    при пустом результате fallback к глобальному поиску.
    """
    diag = {"subs_ok": 0, "subs_failed": 0, "raw_total": 0, "after_filter": 0}

    async with httpx.AsyncClient(follow_redirects=True) as client:
        all_hits: dict[str, dict] = {}

        async def _do_global() -> None:
            try:
                rows = await _search_endpoint(
                    client, query=query, subreddit=None,
                    time_range=time_range, sort=sort, limit=limit,
                )
                for d in rows:
                    all_hits[d.get("id", "")] = d
                diag["subs_ok"] += 1
            except Exception as e:
                logger.warning("reddit global search failed", error=str(e))
                diag["subs_failed"] += 1

        if subreddit:
            try:
                rows = await _search_endpoint(
                    client, query=query, subreddit=subreddit,
                    time_range=time_range, sort=sort, limit=limit,
                )
                for d in rows:
                    all_hits[d.get("id", "")] = d
                diag["subs_ok"] = 1
            except Exception as e:
                logger.warning("reddit single-sub search failed", sub=subreddit, error=str(e))
                diag["subs_failed"] = 1
        elif use_curated:
            import asyncio as _aio
            sem = _aio.Semaphore(6)  # вежливее к Reddit, не 18 одновременно

            async def _one(sr: str) -> tuple[str, list[dict] | Exception]:
                async with sem:
                    try:
                        rows = await _search_endpoint(
                            client, query=query, subreddit=sr,
                            time_range=time_range, sort=sort, limit=10,
                        )
                        return sr, rows
                    except Exception as e:
                        return sr, e

            results = await _aio.gather(*(_one(sr) for sr in CURATED_SUBREDDITS))
            for sr, res in results:
                if isinstance(res, Exception):
                    diag["subs_failed"] += 1
                    logger.warning("reddit sub search failed", sub=sr, error=str(res))
                    continue
                diag["subs_ok"] += 1
                for d in res:
                    all_hits[d.get("id", "")] = d

            # Fallback: если по куратору пусто — добиваем глобальным поиском
            if not all_hits:
                logger.info("curated search empty, falling back to global", query=query[:60])
                await _do_global()
        else:
            await _do_global()

    diag["raw_total"] = len(all_hits)

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
    hits.sort(key=lambda h: h.score, reverse=True)
    diag["after_filter"] = len(hits)
    logger.info("reddit search done", query=query[:60], **diag)
    return hits, diag


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
