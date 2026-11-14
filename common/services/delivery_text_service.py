"""Shipping receipt and chat-copy outbox. No card lookup or consumption on retry.

The unique order key plus row locks cover concurrent workers and MCP calls.
Unknown network outcomes are never automatically retried.
"""
from datetime import timedelta

from sqlalchemy import select, update

from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.models.delivery_text import DeliveryTextAttempt, DeliveryTextReceipt
from common.models.xy_order import XYOrder
from common.utils.time_utils import get_beijing_now_naive

PAID_STATES = {"pending_ship", "pending", "paid", "待发货"}
SHIPPED_STATES = {"shipped", "completed"}
MAX_ATTEMPTS = 3


class DeliveryTextService:
    def __init__(self, session):
        self.session = session

    async def prepare(self, account_id, order_no, card_id, buyer_id, chat_id, content, *, item_id=None):
        """Persist the exact credential BEFORE asking the platform to ship."""
        order = (await self.session.execute(select(XYOrder).where(
            XYOrder.account_id == account_id, XYOrder.order_no == order_no
        ).with_for_update())).scalar_one_or_none()
        if not order:
            raise ValueError("订单不存在")
        if item_id is not None and order.item_id != item_id:
            raise ValueError("订单商品不匹配")
        existing = (await self.session.execute(select(DeliveryTextReceipt).where(
            DeliveryTextReceipt.account_id == account_id, DeliveryTextReceipt.order_no == order_no
        ))).scalar_one_or_none()
        if existing:
            return existing, False
        card = await self.session.get(Card, card_id)
        if (not card or card.user_id != order.owner_id or not card.enabled
                or card.type != "text" or not card.use_no_logistics_form or not card.send_text_after_form):
            raise ValueError("卡券未启用凭证后补发文字")
        relation = await self.session.scalar(select(CardItemRelation.id).where(
            CardItemRelation.card_id == card_id, CardItemRelation.item_id == order.item_id,
            CardItemRelation.source == "own").limit(1))
        if not relation and card.item_id != order.item_id:
            raise ValueError("卡券不属于本订单商品")
        if (order.status not in PAID_STATES or order.card_only_delivered
                or not order.amount or order.amount <= 0):
            raise ValueError("订单未满足付款后发货条件")
        if not buyer_id or str(order.buyer_id or "") != str(buyer_id):
            raise ValueError("订单买家不匹配")
        chat_id = str(chat_id or "").removesuffix("@goofish")
        if not chat_id or (order.chat_id and str(order.chat_id).removesuffix("@goofish") != chat_id):
            raise ValueError("订单会话缺失或不匹配")
        content = str(content or "").strip()
        if not content or len(content) > 2000:
            raise ValueError("凭证文字须为 1–2000 字，避免链接截断")
        # MySQL DATETIME(0) may round fractional seconds up; keep the due time
        # on the stored second so immediate dispatch cannot be delayed by a poll.
        now = get_beijing_now_naive().replace(microsecond=0)
        row = DeliveryTextReceipt(
            owner_id=order.owner_id, account_id=account_id, order_no=order_no,
            card_id=card_id, item_id=order.item_id or "", buyer_id=str(buyer_id), chat_id=chat_id,
            content=content, state="confirming", attempts=0, created_at=now, updated_at=now,
        )
        self.session.add(row)
        await self.session.commit()
        return row, True

    async def shipping_result(self, receipt_id, *, confirmed, method="auto", reason=None):
        row = (await self.session.execute(select(DeliveryTextReceipt).where(
            DeliveryTextReceipt.id == receipt_id).with_for_update())).scalar_one()
        if row.state != "confirming":
            return row
        now = get_beijing_now_naive().replace(microsecond=0)
        row.updated_at = now
        if not confirmed:
            row.state = "shipping_unknown"
            row.reason = reason or "发货接口未确认成功，需核对平台凭证；未发送文字"
        else:
            order = (await self.session.execute(select(XYOrder).where(
                XYOrder.account_id == row.account_id, XYOrder.order_no == row.order_no,
                XYOrder.owner_id == row.owner_id).with_for_update())).scalar_one()
            row.confirmed_at = now
            row.next_attempt_at = now
            if order.status not in PAID_STATES | SHIPPED_STATES:
                row.state, row.reason = "cancelled", "订单已关闭或退款，停止补发文字"
            else:
                order.status = "shipped" if order.status != "completed" else order.status
                order.delivery_method = method
                order.delivery_content = row.content
                order.delivery_fail_reason = None
                row.state, row.reason = "pending", None
            await self.session.execute(update(Card).where(Card.id == row.card_id).values(
                delivery_count=Card.delivery_count + 1))
        await self.session.commit()
        return row

    async def claim(self, receipt_id, mid):
        row = (await self.session.execute(select(DeliveryTextReceipt).where(
            DeliveryTextReceipt.id == receipt_id).with_for_update())).scalar_one_or_none()
        if not row or row.state != "pending" or not row.confirmed_at:
            return None
        now = get_beijing_now_naive().replace(microsecond=0)
        if row.next_attempt_at and row.next_attempt_at > now:
            return None
        order = (await self.session.execute(select(XYOrder).where(
            XYOrder.account_id == row.account_id, XYOrder.order_no == row.order_no,
            XYOrder.owner_id == row.owner_id))).scalar_one_or_none()
        card = await self.session.get(Card, row.card_id)
        if (not order or order.status not in SHIPPED_STATES or not card or not card.enabled
                or not card.send_text_after_form or not card.use_no_logistics_form
                or str(order.buyer_id or "") != row.buyer_id
                or (order.chat_id and str(order.chat_id).removesuffix("@goofish") != row.chat_id)):
            row.state, row.reason = "cancelled", "订单状态、买家、会话或卡券开关已变，停止发送"
            row.updated_at = now
            await self.session.commit()
            return None
        if row.attempts >= MAX_ATTEMPTS:
            return None
        row.state, row.reason = "sending", None
        row.attempts += 1
        row.updated_at = now
        self.session.add(DeliveryTextAttempt(receipt_id=row.id, number=row.attempts, mid=mid,
            state="sending", started_at=now))
        await self.session.commit()
        return row

    async def finish(self, receipt_id, mid, state, reason=None):
        if state not in {"success", "failed", "unknown"}:
            raise ValueError("无效发送结果")
        row = (await self.session.execute(select(DeliveryTextReceipt).where(
            DeliveryTextReceipt.id == receipt_id).with_for_update())).scalar_one()
        attempt = (await self.session.execute(select(DeliveryTextAttempt).where(
            DeliveryTextAttempt.receipt_id == receipt_id, DeliveryTextAttempt.mid == mid,
            DeliveryTextAttempt.number == row.attempts))).scalar_one_or_none()
        if not attempt or row.state != "sending":
            return
        now = get_beijing_now_naive().replace(microsecond=0)
        row.state, row.reason, row.updated_at = state, reason, now
        attempt.state, attempt.reason, attempt.finished_at = state, reason, now
        await self.session.commit()

    async def retry(self, account_id, order_no, owner_id):
        row = (await self.session.execute(select(DeliveryTextReceipt).where(
            DeliveryTextReceipt.account_id == account_id, DeliveryTextReceipt.order_no == order_no,
            DeliveryTextReceipt.owner_id == owner_id).with_for_update())).scalar_one_or_none()
        if not row:
            raise ValueError("无本功能的发货凭证记录，不会为历史订单生成新卡券")
        if row.state in {"pending", "sending", "success"}:
            return self.summary(row)
        if row.state != "failed" or not row.confirmed_at or row.attempts >= MAX_ATTEMPTS:
            raise ValueError("仅可重试明确失败的文字发送（最多3次）；未知结果须先核实，不能重发")
        row.state, row.reason = "pending", None
        row.updated_at = row.next_attempt_at = get_beijing_now_naive().replace(microsecond=0)
        await self.session.commit()
        return self.summary(row)

    async def recover_stale(self):
        """A crash cannot prove a send failed. Never reset a sending lease to pending."""
        now = get_beijing_now_naive().replace(microsecond=0)
        stale = now - timedelta(minutes=5)
        for old, new in (("sending", "unknown"), ("confirming", "shipping_unknown")):
            await self.session.execute(update(DeliveryTextReceipt).where(
                DeliveryTextReceipt.state == old, DeliveryTextReceipt.updated_at < stale
            ).values(state=new, reason="处理已中断，结果待核实；未自动重试", updated_at=now))
        await self.session.execute(update(DeliveryTextAttempt).where(
            DeliveryTextAttempt.state == "sending", DeliveryTextAttempt.started_at < stale
        ).values(state="unknown", reason="发送已中断，结果待核实", finished_at=now))
        await self.session.commit()

    async def get(self, account_id, order_no, owner_id):
        row = (await self.session.execute(select(DeliveryTextReceipt).where(
            DeliveryTextReceipt.account_id == account_id, DeliveryTextReceipt.order_no == order_no,
            DeliveryTextReceipt.owner_id == owner_id))).scalar_one_or_none()
        if not row:
            return None
        attempts = (await self.session.execute(select(DeliveryTextAttempt).where(
            DeliveryTextAttempt.receipt_id == row.id).order_by(DeliveryTextAttempt.number))).scalars().all()
        return {**self.summary(row), "content": row.content, "history": [
            {"number": a.number, "mid": a.mid, "state": a.state, "reason": a.reason,
             "started_at": a.started_at.isoformat(),
             "finished_at": a.finished_at.isoformat() if a.finished_at else None} for a in attempts]}

    async def summaries(self, orders):
        if not orders:
            return {}
        rows = (await self.session.execute(select(DeliveryTextReceipt).where(
            DeliveryTextReceipt.order_no.in_([o.order_no for o in orders]),
            DeliveryTextReceipt.owner_id.in_({o.owner_id for o in orders})
        ))).scalars().all()
        allowed = {(o.owner_id, o.account_id, o.order_no) for o in orders}
        return {(r.account_id, r.order_no): self.summary(r) for r in rows
                if (r.owner_id, r.account_id, r.order_no) in allowed}

    @staticmethod
    def summary(row):
        return {"state": row.state, "attempts": row.attempts, "reason": row.reason,
                "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
                "updated_at": row.updated_at.isoformat(),
                "can_retry": row.state == "failed" and row.attempts < MAX_ATTEMPTS}
