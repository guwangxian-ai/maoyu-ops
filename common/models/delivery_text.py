"""Durable, order-scoped copy of a successful shipping credential."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base


class DeliveryTextReceipt(Base):
    __tablename__ = "xy_delivery_text_receipts"
    __table_args__ = (
        UniqueConstraint("account_id", "order_no", name="uq_delivery_text_order"),
        Index("idx_delivery_text_pending", "state", "next_attempt_at"),
        Index("idx_delivery_text_owner", "owner_id", "order_no"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    account_id: Mapped[str] = mapped_column(String(64))
    order_no: Mapped[str] = mapped_column(String(64))
    card_id: Mapped[int] = mapped_column(BigInteger)
    item_id: Mapped[str] = mapped_column(String(64))
    buyer_id: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)


class DeliveryTextAttempt(Base):
    __tablename__ = "xy_delivery_text_attempts"
    __table_args__ = (UniqueConstraint("receipt_id", "number", name="uq_delivery_text_attempt"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    receipt_id: Mapped[int] = mapped_column(BigInteger, index=True)
    number: Mapped[int] = mapped_column(Integer)
    mid: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
