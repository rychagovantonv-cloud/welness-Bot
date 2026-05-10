"""Универсальный RSS-парсер с догрузкой full-text через trafilatura если feed truncated."""

import asyncio
from datetime import datetime, timezone

import feedparser
import httpx
import trafilatura
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from parsers.base import RawItem

# Если RSS-описание короче этого порога, считаем что feed truncated и идём за full text.
TRUNCATED_THRESHOLD = 600


class RSSParser:
    """Один источник = один RSSParser. Имя `source` сохраняется в parsed_items.source."""

    def __init__(self, name: str, feed_url: str, max_items: int = 15):
        self.name = name
        self.feed_url = feed_url
        self.max_items = max_items

    async def fetch(self) -> list[RawItem]:
        async with httpx.AsyncClient(
            headers={"User-Agent": "welness-bot/0.1 (+contact: rychagovantonv-cloud)"},
            follow_redirects=True,
            timeout=20,
        ) as client:
            try:
                feed_text = await self._fetch_feed(client)
            except Exception as e:
                logger.error("rss fetch failed", source=self.name, error=str(e))
                return []

            parsed = feedparser.parse(feed_text)
            entries = parsed.entries[: self.max_items]
            logger.info("rss feed parsed", source=self.name, entries=len(entries))

            tasks = [self._build_item(client, e) for e in entries]
            items = await asyncio.gather(*tasks, return_exceptions=True)

        result: list[RawItem] = []
        for it in items:
            if isinstance(it, Exception):
                logger.warning("rss item failed", source=self.name, error=str(it))
                continue
            if it is not None:
                result.append(it)
        return result

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8), reraise=True)
    async def _fetch_feed(self, client: httpx.AsyncClient) -> str:
        r = await client.get(self.feed_url)
        r.raise_for_status()
        return r.text

    async def _build_item(self, client: httpx.AsyncClient, entry: dict) -> RawItem | None:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            return None

        body = self._extract_body_from_entry(entry)
        if len(body) < TRUNCATED_THRESHOLD:
            full = await self._fetch_full_text(client, link)
            if full and len(full) > len(body):
                body = full

        published = self._extract_date(entry)
        return RawItem(
            source=self.name,
            external_id=link,
            title=title,
            body=body,
            url=link,
            published_at=published,
        )

    @staticmethod
    def _extract_body_from_entry(entry: dict) -> str:
        contents = entry.get("content") or []
        for c in contents:
            value = (c.get("value") or "").strip()
            if value:
                return _strip_html(value)
        summary = entry.get("summary") or entry.get("description") or ""
        return _strip_html(summary).strip()

    @staticmethod
    def _extract_date(entry: dict) -> datetime | None:
        for key in ("published_parsed", "updated_parsed"):
            t = entry.get(key)
            if t:
                try:
                    return datetime(*t[:6], tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    continue
        return None

    async def _fetch_full_text(self, client: httpx.AsyncClient, url: str) -> str:
        try:
            r = await client.get(url, timeout=15)
            r.raise_for_status()
        except Exception as e:
            logger.warning("full-text fetch failed", url=url, error=str(e))
            return ""

        extracted = trafilatura.extract(
            r.text,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        return extracted or ""


def _strip_html(text: str) -> str:
    """Лёгкое раздевание HTML без лишних зависимостей."""
    import re

    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# Стартовый набор источников для Phase 1.
# BBC и Guardian Travel — стабильные, full-content RSS.
# Дальше расширяем после ручной валидации других feed'ов.
DEFAULT_RSS_SOURCES: dict[str, str] = {
    "guardian_travel": "https://www.theguardian.com/travel/rss",
    "bbc_travel": "https://www.bbc.com/travel/feed.rss",
}


def make_default_rss_parsers(max_items: int = 10) -> list[RSSParser]:
    return [RSSParser(name=k, feed_url=v, max_items=max_items) for k, v in DEFAULT_RSS_SOURCES.items()]
