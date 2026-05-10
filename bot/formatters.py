from html import escape

from ai_engine.schemas import RadarCard

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
