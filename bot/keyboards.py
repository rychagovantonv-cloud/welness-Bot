from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CB_APPROVE_PREFIX = "rd_app:"
CB_TRASH_PREFIX = "rd_trash:"
CB_INSIGHT_SAVE_PREFIX = "in_save:"
CB_INSIGHT_SKIP_PREFIX = "in_skip:"
CB_INSIGHT_RUN_PREFIX = "in_run:"
CB_AEO_SAVE_PREFIX = "ae_save:"
CB_AEO_SKIP_PREFIX = "ae_skip:"


def radar_card_kb(parsed_item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ В работу", callback_data=f"{CB_APPROVE_PREFIX}{parsed_item_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Мусор", callback_data=f"{CB_TRASH_PREFIX}{parsed_item_id}"
                ),
            ]
        ]
    )


def insight_run_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔍 Разобрать",
                    callback_data=f"{CB_INSIGHT_RUN_PREFIX}{token}",
                )
            ]
        ]
    )


def insight_save_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📄 Сохранить отчёт",
                    callback_data=f"{CB_INSIGHT_SAVE_PREFIX}{token}",
                ),
                InlineKeyboardButton(
                    text="✖️ Пропустить",
                    callback_data=f"{CB_INSIGHT_SKIP_PREFIX}{token}",
                ),
            ]
        ]
    )


def aeo_save_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📄 Сохранить AEO-отчёт",
                    callback_data=f"{CB_AEO_SAVE_PREFIX}{token}",
                ),
                InlineKeyboardButton(
                    text="✖️ Пропустить",
                    callback_data=f"{CB_AEO_SKIP_PREFIX}{token}",
                ),
            ]
        ]
    )
