"""Rotate the newly initialized admin password inside the backend-web container.

Usage: python /tmp/rotate_admin.py /tmp/admin-password
The password is read from a mode-600 file and is never printed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, "/app/backend-web")

from app.core.security import get_password_hash, verify_password  # noqa: E402
from common.db.session import async_session_maker  # noqa: E402


async def rotate(password_file: Path) -> None:
    password = password_file.read_text(encoding="utf-8").rstrip("\n")
    if len(password) < 20:
        raise ValueError("admin password must contain at least 20 characters")

    async with async_session_maker() as session:
        result = await session.execute(
            text(
                "UPDATE xy_users SET password_hash = :password_hash, "
                "updated_at = NOW() WHERE username = 'admin' AND role = 'ADMIN'"
            ),
            {"password_hash": get_password_hash(password)},
        )
        if result.rowcount != 1:
            await session.rollback()
            raise RuntimeError("expected exactly one admin account")
        hint = await session.execute(
            text(
                "UPDATE xy_system_settings SET value = 'false', updated_at = NOW() "
                "WHERE `key` = 'show_default_login_info'"
            )
        )
        if hint.rowcount != 1:
            await session.rollback()
            raise RuntimeError("expected exactly one login hint setting")
        await session.commit()
        saved_hash = (
            await session.execute(
                text("SELECT password_hash FROM xy_users WHERE username = 'admin' AND role = 'ADMIN'")
            )
        ).scalar_one()
        if not verify_password(password, saved_hash) or verify_password("admin123", saved_hash):
            raise RuntimeError("admin password verification failed")
        saved_hint = (
            await session.execute(
                text("SELECT value FROM xy_system_settings WHERE `key` = 'show_default_login_info'")
            )
        ).scalar_one()
        if saved_hint != "false":
            raise RuntimeError("default login hint is still enabled")
    print("Admin password rotated; default login hint disabled")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: rotate_admin.py PASSWORD_FILE")
    asyncio.run(rotate(Path(sys.argv[1])))
