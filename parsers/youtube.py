"""YouTube comments parser via YouTube Data API v3.

Принимает любой YouTube URL:
- https://www.youtube.com/watch?v=VIDEO_ID
- https://youtu.be/VIDEO_ID
- https://www.youtube.com/shorts/VIDEO_ID
- https://www.youtube.com/embed/VIDEO_ID
"""

import asyncio
import re
from dataclasses import dataclass

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from loguru import logger

from config import settings

VIDEO_ID_RE = re.compile(
    r"(?:v=|/v/|/embed/|/shorts/|youtu\.be/)([0-9A-Za-z_-]{11})"
)

YOUTUBE_HOST_RE = re.compile(r"^https?://(?:[\w-]+\.)*(?:youtube\.com|youtu\.be)/", re.IGNORECASE)


@dataclass
class YouTubeComment:
    author: str
    text: str
    likes: int
    is_reply: bool


@dataclass
class YouTubeVideo:
    video_id: str
    title: str
    channel: str
    description: str
    view_count: int
    comments: list[YouTubeComment]

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def slug(self) -> str:
        return f"yt-{self.video_id}"


def is_youtube_url(url: str) -> bool:
    return bool(YOUTUBE_HOST_RE.match(url.strip()))


def extract_video_id(url: str) -> str | None:
    m = VIDEO_ID_RE.search(url)
    return m.group(1) if m else None


def _build_client():
    if not settings.youtube_api_key:
        raise RuntimeError("YOUTUBE_API_KEY not configured")
    return build(
        "youtube", "v3",
        developerKey=settings.youtube_api_key.get_secret_value(),
        cache_discovery=False,
    )


def _fetch_video_meta_sync(client, video_id: str) -> dict | None:
    try:
        resp = client.videos().list(
            part="snippet,statistics", id=video_id, maxResults=1
        ).execute()
    except HttpError as e:
        logger.error("youtube videos.list failed", video=video_id, error=str(e))
        return None
    items = resp.get("items", [])
    if not items:
        return None
    sn = items[0].get("snippet", {})
    st = items[0].get("statistics", {})
    return {
        "title": sn.get("title", "")[:300],
        "channel": sn.get("channelTitle", "")[:200],
        "description": (sn.get("description") or "")[:3000],
        "view_count": int(st.get("viewCount", 0)),
    }


def _fetch_comments_sync(
    client, video_id: str, *, top_n: int = 80, include_replies: bool = True
) -> list[YouTubeComment]:
    out: list[YouTubeComment] = []
    page_token = None
    fetched = 0
    while fetched < top_n:
        try:
            resp = client.commentThreads().list(
                part="snippet,replies",
                videoId=video_id,
                order="relevance",  # YouTube sorts by relevance/likes
                maxResults=min(100, top_n - fetched),
                textFormat="plainText",
                pageToken=page_token,
            ).execute()
        except HttpError as e:
            # 403 = comments disabled or quota; 404 = video not found
            logger.error("youtube commentThreads.list failed", video=video_id, error=str(e))
            break

        for thread in resp.get("items", []):
            top = thread["snippet"]["topLevelComment"]["snippet"]
            out.append(
                YouTubeComment(
                    author=top.get("authorDisplayName", "[unknown]")[:80],
                    text=(top.get("textDisplay") or "")[:1500],
                    likes=int(top.get("likeCount", 0)),
                    is_reply=False,
                )
            )
            fetched += 1
            if include_replies:
                replies = (thread.get("replies") or {}).get("comments", [])
                for r in replies[:3]:  # топ-3 ответа на коммент
                    rs = r["snippet"]
                    out.append(
                        YouTubeComment(
                            author=rs.get("authorDisplayName", "[unknown]")[:80],
                            text=(rs.get("textDisplay") or "")[:800],
                            likes=int(rs.get("likeCount", 0)),
                            is_reply=True,
                        )
                    )
            if fetched >= top_n:
                break

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    # Сортируем top-level по лайкам desc, replies оставляем рядом с их родителями
    # Простая логика: разделяем, сортируем, объединяем
    top_level = [c for c in out if not c.is_reply]
    replies_by_proximity = [c for c in out if c.is_reply]
    top_level.sort(key=lambda c: c.likes, reverse=True)
    return top_level[:top_n] + replies_by_proximity[: top_n // 4]


async def fetch_video(url: str, *, top_n: int = 80) -> YouTubeVideo | None:
    if not is_youtube_url(url):
        return None
    video_id = extract_video_id(url)
    if not video_id:
        logger.warning("could not extract YouTube video ID from URL", url=url[:120])
        return None

    try:
        client = _build_client()
    except RuntimeError as e:
        logger.error(str(e))
        return None

    # Sync API в thread pool чтобы не блокировать loop
    meta_task = asyncio.to_thread(_fetch_video_meta_sync, client, video_id)
    comments_task = asyncio.to_thread(_fetch_comments_sync, client, video_id, top_n=top_n)
    meta, comments = await asyncio.gather(meta_task, comments_task)

    if meta is None:
        logger.warning("youtube video metadata not found", video=video_id)
        return None

    return YouTubeVideo(
        video_id=video_id,
        title=meta["title"],
        channel=meta["channel"],
        description=meta["description"],
        view_count=meta["view_count"],
        comments=comments,
    )


def format_video_for_llm(video: YouTubeVideo) -> str:
    parts = [
        f"# YouTube video",
        f"# Channel: {video.channel}",
        f"# Title: {video.title}",
        f"# Views: {video.view_count}",
        f"# URL: {video.url}",
        "",
    ]
    if video.description:
        # короткое description, не вся полотенце
        parts.append(f"## Description (труncated):\n{video.description[:1500]}\n")
    parts.append(f"## Top {len(video.comments)} comments (sorted by relevance/likes):\n")
    for i, c in enumerate(video.comments, 1):
        prefix = "  └─ " if c.is_reply else ""
        parts.append(f"[{i}]{prefix}{c.author} (likes={c.likes}):")
        parts.append(f"{prefix}{c.text}\n")
    return "\n".join(parts)
