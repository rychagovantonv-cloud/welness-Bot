from typing import Literal

from pydantic import BaseModel, Field

TransformationType = Literal[
    "healing", "adventure", "identity_shift", "solo_growth", "science", "drafts"
]


class RadarCard(BaseModel):
    """Одна карточка-результат LLM-обработки сырой статьи."""

    external_id: str = Field(description="external_id исходного RawItem (URL или PMID)")
    title: str
    url: str
    source: str = Field(description="имя парсера: pubmed, guardian_travel, bbc_travel, ...")
    is_trash: bool = Field(
        description=(
            "true = listicle/инстаграм-туризм/банальное 'топ-10', не нести в TG. "
            "false = есть содержательный insight для Reflective Traveler."
        )
    )
    summary: str = Field(
        description=(
            "Если is_trash=false: сухая выжимка инсайта в 2-4 предложения, без воды и "
            "общих фраз. Опирается на конкретный факт/наблюдение/механизм. "
            "Если is_trash=true: одна фраза почему мусор."
        )
    )
    transformation_type: TransformationType = Field(
        description=(
            "Куда положить контент: healing (восстановление, выгорание, психотерапия, "
            "соматика), adventure (вызов, дискомфорт, выход из зоны), "
            "identity_shift (переосмысление 'кто я'), solo_growth (одиночные путешествия, "
            "саморазвитие), science (нейронаука/исследования без явной привязки), "
            "drafts (если категория неочевидна)."
        )
    )
    relevance: Literal["high", "medium", "low"] = Field(
        description="high: уникальный угол; medium: ок но видали; low: edge case."
    )


class RadarBatchResult(BaseModel):
    cards: list[RadarCard]
