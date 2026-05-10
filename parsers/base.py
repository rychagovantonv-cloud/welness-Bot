import hashlib
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field


class RawItem(BaseModel):
    """Унифицированный объект, возвращаемый любым парсером."""

    source: str
    external_id: str
    title: str
    body: str
    url: str
    published_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)
    content_hash: str = ""

    def model_post_init(self, _: object) -> None:
        if not self.content_hash:
            payload = f"{self.title}\n{self.body}".encode("utf-8", errors="replace")
            self.content_hash = hashlib.sha256(payload).hexdigest()


class Parser(Protocol):
    name: str

    async def fetch(self) -> list[RawItem]: ...
