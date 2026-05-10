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


# ---- Insight (Бот №2 / Phase 2) ----

PainCategory = Literal["fear", "desire", "meaning_crisis", "frustration"]


class PainPoint(BaseModel):
    category: PainCategory = Field(
        description=(
            "fear — страх (потерять, не справиться, остаться одному); "
            "desire — желание (что хочется ощутить, кем стать, что получить); "
            "meaning_crisis — кризис смысла (зачем я это делаю, кто я, ради чего); "
            "frustration — раздражение/злость на конкретный аспект."
        )
    )
    title: str = Field(
        description="Короткий заголовок боли на русском, 3-7 слов. Без воды."
    )
    description: str = Field(
        description=(
            "2-3 предложения на русском: что именно болит, как проявляется, "
            "за чем стоит. Конкретно, без 'люди часто чувствуют...'."
        )
    )
    representative_quotes: list[str] = Field(
        description=(
            "2-3 ОРИГИНАЛЬНЫЕ цитаты из комментариев (на языке оригинала, обычно "
            "английский). НЕ переводить. Это голос ЦА. Цитата = одно-два "
            "предложения, не весь коммент."
        ),
        min_length=1,
        max_length=4,
    )
    frequency: int = Field(
        description=(
            "Грубая оценка: в скольких комментариях треда эта боль явно или "
            "косвенно прозвучала. Если только OP — ставь 1."
        ),
        ge=1,
    )


class InsightReport(BaseModel):
    """Структурированный отчёт по треду / видео под аудиторию Reflective Traveler."""

    audience_segment: str = Field(
        description=(
            "1-2 предложения на русском: кто эти люди в данном треде. "
            "Точнее чем 'путешественники' — возраст/жизненный этап/контекст. "
            "Например: 'женщины 35-45 после развода, ищут переустановку идентичности "
            "через одиночные путешествия в Юго-Восточной Азии'."
        )
    )
    pain_points: list[PainPoint] = Field(
        description="Топ 3-7 болевых точек, отсортированные по силе/частотности.",
        min_length=1,
        max_length=10,
    )
    desires: list[str] = Field(
        description=(
            "Что они хотят получить от опыта. Не 'хорошо отдохнуть', а конкретные "
            "сдвиги: 'почувствовать что я снова целая', 'разрешить себе быть слабой', "
            "'поверить что есть жизнь после X'. На русском, 3-7 пунктов."
        ),
        min_length=1,
    )
    triggers: list[str] = Field(
        description=(
            "Что запускает поиск решения. События/состояния которые приводят их "
            "сюда: 'годовщина утраты', 'конец долгого проекта', 'бернаут', "
            "'дети уехали учиться'. На русском, 3-6 пунктов."
        ),
        min_length=1,
    )
    summary_for_aeo: str = Field(
        description=(
            "Короткий абзац (3-5 предложений) на русском, который можно использовать "
            "как сырьё для AEO/SEO контента: ключевые формулировки, темы, голос. "
            "Без маркетинговой речи. Это рабочий конспект, не реклама."
        )
    )


# ---- AEO (Answer Engine Optimization) ----


class AeoModelResponse(BaseModel):
    model: str = Field(description="Имя модели: 'claude-haiku-4-5' / 'gemini-2.5-flash' / ...")
    raw_text: str = Field(description="Полный ответ модели на запрос — без обработки.")


class AeoAnalysis(BaseModel):
    """Мета-анализ ответов нескольких AI-моделей на один запрос."""

    common_themes: list[str] = Field(
        description=(
            "Темы и формулировки, которые встречаются у всех моделей. "
            "На русском, 3-7 пунктов. Это 'дефолтный нарратив' AI про ваш сегмент."
        ),
        min_length=1,
    )
    unique_angles: dict[str, list[str]] = Field(
        description=(
            "Что уникально у каждой модели. Ключ — имя модели, значение — список "
            "уникальных тем/брендов/рекомендаций которые есть только у неё. "
            "На русском."
        ),
    )
    dominant_narrative: str = Field(
        description=(
            "1-2 предложения на русском: как AI в среднем описывает запрашиваемую нишу. "
            "Какой сторителлинг доминирует, кого цитируют, какие места/практики называют."
        )
    )
    content_gaps: list[str] = Field(
        description=(
            "Где wellness-бренд может вклиниться: темы, которые AI обходит "
            "стороной либо описывает поверхностно. На русском, 3-6 пунктов. "
            "Это карта возможностей для контента."
        ),
        min_length=1,
    )
    recommended_keywords: list[str] = Field(
        description=(
            "Ключевые фразы и формулировки которые AI повторяет — это сигнал какие "
            "запросы они уже понимают. Используется для AEO-контента. 5-12 пунктов, "
            "на оригинальном языке (англ если ответы на англ)."
        ),
        min_length=1,
    )
    summary: str = Field(
        description=(
            "3-5 предложений на русском: что фаундерам делать с этой информацией. "
            "Конкретно. Не 'нужно создавать контент'."
        )
    )
