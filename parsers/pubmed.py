"""PubMed parser via NCBI E-utilities — без ключа, мягкий rate limit."""

import asyncio
from datetime import datetime
from xml.etree import ElementTree as ET

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from parsers.base import RawItem

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# Темы релевантные Reflective Traveler: трансформация, нейронаука сознания, психотерапия
DEFAULT_QUERY = (
    '("psilocybin"[MeSH] OR "meditation"[MeSH] OR "mindfulness"[MeSH] OR '
    '"awe"[Title/Abstract] OR "ego dissolution"[Title/Abstract] OR '
    '"transformative experience"[Title/Abstract] OR "psychedelic"[Title/Abstract] OR '
    '"breathwork"[Title/Abstract] OR "default mode network"[Title/Abstract]) '
    'AND (review[ptyp] OR "systematic review"[ptyp] OR "meta-analysis"[ptyp])'
)


class PubMedParser:
    name = "pubmed"

    def __init__(self, query: str = DEFAULT_QUERY, max_results: int = 20, days_back: int = 14):
        self.query = query
        self.max_results = max_results
        self.days_back = days_back

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
    async def _esearch(self, client: httpx.AsyncClient) -> list[str]:
        params = {
            "db": "pubmed",
            "term": self.query,
            "retmax": self.max_results,
            "sort": "date",
            "retmode": "json",
            "reldate": self.days_back,
            "datetype": "pdat",
        }
        r = await client.get(ESEARCH, params=params, timeout=20)
        r.raise_for_status()
        return r.json().get("esearchresult", {}).get("idlist", [])

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
    async def _efetch(self, client: httpx.AsyncClient, ids: list[str]) -> str:
        if not ids:
            return ""
        params = {
            "db": "pubmed",
            "id": ",".join(ids),
            "retmode": "xml",
            "rettype": "abstract",
        }
        r = await client.get(EFETCH, params=params, timeout=30)
        r.raise_for_status()
        return r.text

    async def fetch(self) -> list[RawItem]:
        async with httpx.AsyncClient(headers={"User-Agent": "welness-bot/0.1"}) as client:
            try:
                ids = await self._esearch(client)
            except Exception as e:
                logger.error("pubmed esearch failed: {}", e)
                return []

            if not ids:
                logger.info("pubmed: no new results", query=self.query[:80])
                return []

            try:
                xml = await self._efetch(client, ids)
            except Exception as e:
                logger.error("pubmed efetch failed: {}", e)
                return []

        items = self._parse_xml(xml)
        logger.info("pubmed parsed", count=len(items), ids_total=len(ids))
        # Лёгкая задержка чтобы не упереться в rate limit на следующих джобах
        await asyncio.sleep(0.5)
        return items

    @staticmethod
    def _parse_xml(xml: str) -> list[RawItem]:
        if not xml.strip():
            return []
        items: list[RawItem] = []
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as e:
            logger.error("pubmed xml parse error: {}", e)
            return []

        for art in root.findall(".//PubmedArticle"):
            pmid_el = art.find(".//PMID")
            title_el = art.find(".//ArticleTitle")
            if pmid_el is None or pmid_el.text is None or title_el is None:
                continue
            pmid = pmid_el.text.strip()
            title = "".join(title_el.itertext()).strip()

            abstract_parts = [
                "".join(t.itertext()).strip() for t in art.findall(".//AbstractText")
            ]
            body = "\n\n".join(p for p in abstract_parts if p)

            pub_date = None
            year_el = art.find(".//PubDate/Year")
            month_el = art.find(".//PubDate/Month")
            if year_el is not None and year_el.text:
                try:
                    year = int(year_el.text)
                    month = 1
                    if month_el is not None and month_el.text:
                        m = month_el.text
                        try:
                            month = int(m)
                        except ValueError:
                            month = datetime.strptime(m[:3], "%b").month
                    pub_date = datetime(year, month, 1)
                except (ValueError, TypeError):
                    pass

            items.append(
                RawItem(
                    source="pubmed",
                    external_id=pmid,
                    title=title,
                    body=body,
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    published_at=pub_date,
                )
            )
        return items
