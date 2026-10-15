"""Validate published cloud assets immediately before producing delivery text.

This module only reads the project's database. Provider credentials and live cloud
requests belong to the publishing workflow, never to an order's delivery path.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession


class CloudAssetDeliveryError(ValueError):
    """An associated asset cannot safely supply this card's delivery content."""


_PROVIDERS = {
    "quark": ("夸克网盘", "pan.quark.cn", ("/s/",)),
    "baidu": ("百度网盘", "pan.baidu.com", ("/s/", "/share/")),
}


def _field(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _plain_text(value: Any, maximum: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum and not any(
        ord(char) < 32 or ord(char) == 127 for char in value
    )


def render_cloud_asset_delivery(snapshot: dict, *, now: datetime | None = None) -> str:
    """Render a complete published snapshot, rejecting missing or expired links.

    ``required_providers`` is captured at publication time, so an unfinished new
    version cannot change the platform requirements of the last published one.
    ISO timestamps without an offset use the project's UTC storage convention.
    """
    if not isinstance(snapshot, dict):
        raise CloudAssetDeliveryError("资料发布快照无效，请重新发布")
    version, name = snapshot.get("version"), snapshot.get("name")
    if type(version) is not int or version < 1 or not _plain_text(name, 255):
        raise CloudAssetDeliveryError("资料发布版本或名称无效，请重新发布")
    required = snapshot.get("required_providers")
    if (not isinstance(required, list) or not required
            or any(not isinstance(provider, str) or provider not in _PROVIDERS for provider in required)
            or len(required) != len(set(required))):
        raise CloudAssetDeliveryError("资料发布快照缺少有效的平台清单，请重新发布")
    targets = snapshot.get("targets")
    if not isinstance(targets, list) or len(targets) != len(required):
        raise CloudAssetDeliveryError("资料分享平台不完整，请补齐所有平台后重新发布")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    validated: dict[str, tuple[str, str]] = {}
    for target in targets:
        if not isinstance(target, dict):
            raise CloudAssetDeliveryError("资料分享记录无效，请重新发布")
        provider = target.get("provider")
        if not isinstance(provider, str) or provider not in required or provider in validated:
            raise CloudAssetDeliveryError("资料分享平台缺失、重复或不受支持，请重新发布")
        label, host, prefixes = _PROVIDERS[provider]
        if target.get("status") != "shared" or not _plain_text(target.get("account_id"), 256):
            raise CloudAssetDeliveryError(f"{label}尚未完成分享，已停止发货")
        link = target.get("share_url")
        valid_link = _plain_text(link, 16384) and not any(c.isspace() or c == "\\" for c in link)
        try:
            parsed = urlsplit(link) if valid_link else None
            valid_link = bool(parsed and parsed.scheme == "https" and parsed.hostname == host
                              and parsed.port in {None, 443} and not parsed.username and not parsed.password
                              and any(parsed.path.startswith(prefix) and len(parsed.path) > len(prefix)
                                      for prefix in prefixes))
        except ValueError:
            valid_link = False
        if not valid_link:
            raise CloudAssetDeliveryError(f"{label}分享链接无效，已停止发货")
        passcode = target.get("passcode") or ""
        if not isinstance(passcode, str) or passcode and not re.fullmatch(r"[A-Za-z0-9]{1,16}", passcode):
            raise CloudAssetDeliveryError(f"{label}提取码无效，已停止发货")
        if "expires_at" not in target:
            raise CloudAssetDeliveryError(f"{label}分享有效期缺失，已停止发货")
        expiration = target["expires_at"]
        if expiration is not None:
            try:
                if not isinstance(expiration, str) or not expiration:
                    raise ValueError
                deadline = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
            except ValueError as exc:
                raise CloudAssetDeliveryError(f"{label}分享有效期无效，已停止发货") from exc
            if deadline <= current:
                raise CloudAssetDeliveryError(f"{label}分享已过期，请更新分享后再发货")
        validated[provider] = (link, passcode)
    sections = [f"{name.strip()}（版本 {version}）"]
    for provider in required:
        link, passcode = validated[provider]
        section = f"{_PROVIDERS[provider][0]}：{link}"
        if passcode:
            section += f"\n提取码：{passcode}"
        sections.append(section)
    return "\n\n".join(sections)


async def resolve_cloud_asset_delivery(session: AsyncSession, card: Any) -> str | None:
    """Return validated published text, or ``None`` for an ordinary card.

    The association is looked up without an owner filter: filtering first could
    conceal a corrupt cross-owner association and accidentally send stale text.
    """
    from common.models.cloud_asset import CloudAsset

    card_id = _field(card, "id")
    if card_id is None:
        raise CloudAssetDeliveryError("卡券编号缺失，无法校验资料关联")
    try:
        result = await session.execute(
            select(CloudAsset).where(CloudAsset.card_id == card_id).execution_options(populate_existing=True)
        )
        assets = result.scalars().all()
    except SQLAlchemyError as exc:
        raise CloudAssetDeliveryError("资料关联查询失败，已停止发货，请检查数据库状态") from exc
    if not assets:
        return None
    if len(assets) != 1:
        raise CloudAssetDeliveryError("卡券关联了多份资料，已停止发货，请修复关联")
    asset = assets[0]
    if _field(card, "user_id") != asset.owner_id:
        raise CloudAssetDeliveryError("资料与卡券归属不一致，已停止发货")
    if _field(card, "type") != "text":
        raise CloudAssetDeliveryError("关联资料必须使用文字卡券，已停止发货")
    snapshot = getattr(asset, "published", None)
    if snapshot is None:
        raise CloudAssetDeliveryError("资料尚未发布完整版本，已停止发货")
    return render_cloud_asset_delivery(snapshot)
