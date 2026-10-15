"""Revocable, user-owned MCP credentials, call audit and durable write receipts."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base, TimestampMixin


class McpAccessToken(TimestampMixin, Base):
    __tablename__ = "xy_mcp_access_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes: Mapped[list] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class McpToolLog(TimestampMixin, Base):
    __tablename__ = "xy_mcp_tool_logs"
    __table_args__ = (Index("idx_mcp_log_owner_created", "owner_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token_id: Mapped[str] = mapped_column(String(32), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))


class McpOperation(TimestampMixin, Base):
    """Owner-scoped write reservation and redacted result, shared by all clients."""
    __tablename__ = "xy_mcp_operations"
    __table_args__ = (
        UniqueConstraint("owner_id", "operation_id", name="uq_mcp_operation_owner_key"),
        Index("idx_mcp_operation_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token_id: Mapped[str] = mapped_column(String(32), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    required_scope: Mapped[str] = mapped_column(String(80), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    response: Mapped[dict | None] = mapped_column(JSON)
