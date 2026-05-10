from html import escape

from ai_engine.schemas import InsightReport, RadarCard

SOURCE_EMOJI = {
    "pubmed": "🧬",
    "guardian_travel": "📰",
    "guardian_science": "🔬",
    "bbc_travel": "📰",
    "bbc_health": "🩺",
    "cntraveler": "✈️",
    "elpais_viajero": "🌍",
    "psyche": "🧠",
    "aeon": "📜",
    "nautilus": "🐚",
    "discover_magazine": "🔭",
    "mit_tech_review": "💡",
}

TRANSFORMATION_EMOJI = {
    "healing": "🌿",
    "adventure": "🏔",
    "identity_shift": "🪞",
    "solo_growth": "🚶",
    "science": "🔬",
    "drafts": "📝",
}


PAIN_EMOJI = {
    "fear": "😨",
    "desire": "💭",
    "meaning_crisis": "🌀",
    "frustration": "😤",
}


def render_insight_header(report: InsightReport, source_label: str, source_url: str) -> str:
    return (
        f"🔍 <b>Insight</b>: {escape(source_label)}\n"
        f"<a href=\"{escape(source_url)}\">источник</a>\n\n"
        f"<b>Сегмент:</b> {escape(report.audience_segment)}\n\n"
        f"<b>Болей найдено:</b> {len(report.pain_points)}  "
        f"<b>·</b>  Желаний: {len(report.desires)}  "
        f"<b>·</b>  Триггеров: {len(report.triggers)}"
    )


def render_pain_point(pp, index: int) -> str:
    emoji = PAIN_EMOJI.get(pp.category, "•")
    parts = [
        f"{emoji} <b>{index}. {escape(pp.title)}</b>  "
        f"<i>{pp.category}</i>  · freq={pp.frequency}",
        "",
        escape(pp.description),
        "",
    ]
    for q in pp.representative_quotes[:3]:
        # Trim long quotes for TG readability
        q_short = q.strip()
        if len(q_short) > 350:
            q_short = q_short[:347] + "..."
        parts.append(f"<blockquote>{escape(q_short)}</blockquote>")
    return "\n".join(parts)


def render_insight_tail(report: InsightReport) -> str:
    desires = "\n".join(f"• {escape(d)}" for d in report.desires)
    triggers = "\n".join(f"• {escape(t)}" for t in report.triggers)
    return (
        f"<b>💭 Желания:</b>\n{desires}\n\n"
        f"<b>⚡ Триггеры:</b>\n{triggers}\n\n"
        f"<b>📝 AEO-конспект:</b>\n{escape(report.summary_for_aeo)}"
    )


def render_radar_card(card: RadarCard) -> str:
    src_e = SOURCE_EMOJI.get(card.source, "🔗")
    tag_e = TRANSFORMATION_EMOJI.get(card.transformation_type, "📝")
    title = escape(card.title)
    summary = escape(card.summary)

    return (
        f"{src_e} <b>{escape(card.source)}</b>  ·  "
        f"{tag_e} <i>{card.transformation_type}</i>  ·  {card.relevance}\n"
        f"\n"
        f"<b>{title}</b>\n"
        f"\n"
        f"{summary}\n"
        f"\n"
        f"<a href=\"{escape(card.url)}\">Источник</a>"
    )
