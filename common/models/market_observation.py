"""Owner-scoped research evidence, separate from the seller catalog and orders."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, JSON, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base, TimestampMixin


class MarketSampleRun(Base):
    __tablename__ = "xy_market_sample_runs"
    __table_args__ = (
        UniqueConstraint("owner_id", "sample_key", name="uk_market_run_owner_key"),
        Index("idx_market_run_scope_time", "owner_id", "scope_key", "started_at"),
        Index("idx_market_run_owner_time", "owner_id", "started_at"),
        Index("idx_market_run_time", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sample_key: Mapped[str] = mapped_column(String(80), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False)
    monitor_task_id: Mapped[int | None] = mapped_column(BigInteger)
    keyword: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    filters: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    pages_requested: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    pages_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(40))
    message: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class MarketObservation(Base):
    __tablename__ = "xy_market_observations"
    __table_args__ = (
        UniqueConstraint("run_id", "item_id", name="uk_market_observation_run_item"),
        Index("idx_market_observation_scope_item", "owner_id", "scope_key", "item_id", "observed_at"),
        Index("idx_market_observation_owner_item", "owner_id", "item_id", "observed_at"),
        Index("idx_market_observation_time", "observed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False)
    item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_source: Mapped[str | None] = mapped_column(String(40))
    want_count: Mapped[int | None] = mapped_column(BigInteger)
    want_source: Mapped[str | None] = mapped_column(String(40))
    want_approximate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    browse_count: Mapped[int | None] = mapped_column(BigInteger)
    browse_source: Mapped[str | None] = mapped_column(String(40))
    tags: Mapped[list | None] = mapped_column(JSON)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class MarketMonitorState(TimestampMixin, Base):
    """Durable circuit breaker. This never changes task/account enable switches."""
    __tablename__ = "xy_market_monitor_states"
    __table_args__ = (Index("idx_market_monitor_owner", "owner_id", "paused_until"),)

    task_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ready")
    error_code: Mapped[str | None] = mapped_column(String(40))
    paused_until: Mapped[datetime | None] = mapped_column(DateTime)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_run_id: Mapped[str | None] = mapped_column(String(32))
