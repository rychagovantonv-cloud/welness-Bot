from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CB_APPROVE_PREFIX = "rd_app:"
CB_TRASH_PREFIX = "rd_trash:"


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
