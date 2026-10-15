"""Versioned delivery assets and their account-bound cloud replicas."""
from __future__ import annotations

from sqlalchemy import BigInteger, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base, TimestampMixin


class CloudAsset(TimestampMixin, Base):
    __tablename__ = "xy_cloud_assets"
    __table_args__ = (Index("idx_cloud_asset_owner_created", "owner_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="staged")
    message: Mapped[str | None] = mapped_column(Text)
    manifest: Mapped[list] = mapped_column(JSON, nullable=False)
    targets: Mapped[list] = mapped_column(JSON, nullable=False)
    published: Mapped[dict | None] = mapped_column(JSON)
    analysis: Mapped[dict | None] = mapped_column(JSON)
    card_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
