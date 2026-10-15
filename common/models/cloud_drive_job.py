"""Persistent progress, plans and results for user-scoped cloud drive jobs."""
from __future__ import annotations

from sqlalchemy import BigInteger, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base, TimestampMixin


class CloudDriveJob(TimestampMixin, Base):
    __tablename__ = "xy_cloud_drive_jobs"
    __table_args__ = (Index("idx_cdj_owner_created", "owner_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="quark")
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    message: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSON, nullable=False)
    progress: Mapped[dict | None] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
