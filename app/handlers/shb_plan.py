from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import StateFilter
from aiogram.fsm.state import default_state
from aiogram.types import Message

from app.access import is_admin, keyboard_for_message
from app.chat_utils import is_group_chat
from app.config import Settings
from app.handlers.flows import answer_text
from app.keyboards import BTN_SHB_PLAN
from app.services.shb_plan_tasks import ShbPlanTasksError, ShbPlanTasksService
from app.texts import (
    MSG_ADMIN_ACCESS_DENIED,
    MSG_SHB_PLAN_DONE,
    MSG_SHB_PLAN_ERROR,
    MSG_SHB_PLAN_PARTIAL,
    MSG_SHB_PLAN_RUNNING,
)

logger = logging.getLogger(__name__)

router = Router(name="shb_plan")


def _user_id(message: Message) -> int | None:
    return message.from_user.id if message.from_user else None


@router.message(F.text == BTN_SHB_PLAN, StateFilter(default_state))
async def handle_shb_plan_button(
    message: Message,
    bot: Bot,
    settings: Settings,
    shb_plan_tasks: ShbPlanTasksService,
) -> None:
    if is_group_chat(message):
        return
    if not is_admin(settings, _user_id(message)):
        await answer_text(
            message,
            MSG_ADMIN_ACCESS_DENIED,
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return

    await answer_text(
        message,
        MSG_SHB_PLAN_RUNNING,
        reply_markup=keyboard_for_message(settings, message),
        parse_mode=ParseMode.HTML,
    )
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    try:
        result = await shb_plan_tasks.run_and_notify(bot)
    except ShbPlanTasksError as exc:
        logger.exception("SHB plan tasks failed")
        await answer_text(
            message,
            MSG_SHB_PLAN_ERROR.format(error=str(exc)),
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception as exc:
        logger.exception("SHB plan unexpected failure")
        await answer_text(
            message,
            MSG_SHB_PLAN_ERROR.format(error=f"{type(exc).__name__}: {exc}"),
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return

    if result.failed:
        await answer_text(
            message,
            MSG_SHB_PLAN_PARTIAL.format(
                created=len(result.created_task_ids),
                failed=len(result.failed),
                details="\n".join(result.failed[:8]),
            ),
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return

    await answer_text(
        message,
        MSG_SHB_PLAN_DONE.format(created=len(result.created_task_ids)),
        reply_markup=keyboard_for_message(settings, message),
        parse_mode=ParseMode.HTML,
    )
