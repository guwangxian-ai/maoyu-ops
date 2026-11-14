"""Shipping credential + one durable plaintext copy, shared by all delivery entrypoints."""
import asyncio
from datetime import timedelta

from loguru import logger
from sqlalchemy import select, update

from common.db.session import async_session_maker
from common.models.delivery_text import DeliveryTextReceipt
from common.services.delivery_text_service import DeliveryTextService
from common.utils.time_utils import get_beijing_now_naive
from common.utils.xianyu_utils import generate_mid


async def deliver_form_with_text(parent, *, order_no, item_id, card_id, buyer_id, chat_id,
                                 content, method="auto"):
    """A repeated call reads the original receipt; it never consumes a fresh card."""
    if not parent.is_auto_confirm_enabled() or parent.is_only_send_card_enabled():
        raise ValueError("凭证发货需要开启自动确认发货并关闭只发卡券")
    async with async_session_maker() as session:
        row, created = await DeliveryTextService(session).prepare(
            parent.cookie_id, order_no, card_id, buyer_id, chat_id, content, item_id=item_id)
        receipt_id = row.id
        if not created:
            if not row.confirmed_at:
                raise ValueError("本单凭证发货结果待核实，不重复确认或发送")
            return {"content": row.content, "platform_shipping_confirmed": True,
                    "form_only_delivered": True, "text_receipt_id": row.id,
                    "delivery_text": DeliveryTextService.summary(row), "replayed": True}

    # No transaction/row lock is held while calling the platform.
    try:
        result = await parent.auto_delivery_handler.auto_confirm(order_no, item_id, trade_text=content)
        message = str((result or {}).get("message", ""))
        # "Already shipped" does not prove our credential was submitted by this call.
        confirmed = bool(result and result.get("success") and not result.get("skipped_only_send_card")
                         and not result.get("already_delivered") and not result.get("already_shipped")
                         and "ORDER_ALREADY_DELIVERY" not in message and "已发货成功" not in message)
    except Exception:
        confirmed = False
    async with async_session_maker() as session:
        row = await DeliveryTextService(session).shipping_result(receipt_id, confirmed=confirmed, method=method)
        summary = DeliveryTextService.summary(row)
    if not confirmed:
        raise ValueError("平台凭证未获得明确成功回执，文字未发送，请核对原订单")

    # A websocket callback must return before waiting for ACKs from that websocket.
    parent._create_tracked_task(dispatch_receipt(receipt_id, parent))
    return {"content": content, "platform_shipping_confirmed": True, "form_only_delivered": True,
            "text_receipt_id": receipt_id, "delivery_text": summary}


def connected(parent):
    if parent is None or getattr(parent, "ws", None) is None:
        return False
    ws = parent.ws
    if getattr(ws, "closed", False):
        return False
    state = getattr(ws, "state", None)
    return state is None or getattr(state, "name", str(state)) in {"OPEN", "1"}


async def dispatch_receipt(receipt_id, parent, *, ack_timeout=15):
    if not connected(parent):
        async with async_session_maker() as session:
            await session.execute(update(DeliveryTextReceipt).where(
                DeliveryTextReceipt.id == receipt_id, DeliveryTextReceipt.state == "pending"
            ).values(next_attempt_at=get_beijing_now_naive() + timedelta(seconds=30), reason="账号未连接，等待恢复"))
            await session.commit()
        return
    mid = generate_mid()
    async with async_session_maker() as session:
        row = await DeliveryTextService(session).claim(receipt_id, mid)
        if not row:
            return
        # Bind sending identity to the persisted order, not caller-provided content/recipient.
        if row.account_id != parent.cookie_id:
            await DeliveryTextService(session).finish(receipt_id, mid, "failed", "发货账号不匹配，未发送")
            return
        text, chat_id, buyer_id = row.content.replace("######", "\n"), row.chat_id, row.buyer_id
    state, reason = "unknown", "未收到明确的平台发送回执；请核对聊天，不自动重发"
    future = None
    try:
        result = await parent.send_msg(parent.ws, chat_id, buyer_id, text, message_id=mid, log_content=False)
        future = (result or {}).get("send_future")
        if result and result.get("success") and future is not None:
            response = await asyncio.wait_for(asyncio.shield(future), timeout=ack_timeout)
            rejection = parent._extract_send_reject_reason(response)
            if rejection:
                state, reason = "failed", "平台拒绝消息，请核对内容或账号风控后再重试"
            elif isinstance(response, dict) and str(response.get("code", "")) == "200":
                state, reason = "success", None
        # Send exceptions can occur AFTER the socket accepted the data; remain unknown.
    except asyncio.CancelledError:
        # Durable sending lease becomes unknown on recovery, never pending.
        raise
    except Exception:
        pass
    finally:
        parent._pending_mid_futures.pop(mid, None)
        if future is not None and not future.done():
            future.cancel()
    async with async_session_maker() as session:
        await DeliveryTextService(session).finish(receipt_id, mid, state, reason)
    logger.info("凭证后文字发送 receipt={} state={}", receipt_id, state)


async def run_delivery_text_outbox(manager):
    """Bounded recovery for never-sent pending copies, including after reconnect."""
    while True:
        try:
            async with async_session_maker() as session:
                service = DeliveryTextService(session)
                await service.recover_stale()
                rows = (await session.execute(select(DeliveryTextReceipt.id, DeliveryTextReceipt.account_id).where(
                    DeliveryTextReceipt.state == "pending",
                    DeliveryTextReceipt.next_attempt_at <= get_beijing_now_naive()
                ).order_by(DeliveryTextReceipt.next_attempt_at, DeliveryTextReceipt.id).limit(20))).all()
            for receipt_id, account_id in rows:
                await dispatch_receipt(receipt_id, manager.instances.get(account_id))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("凭证后文字队列暂不可用: {}", type(exc).__name__)
        await asyncio.sleep(30)
