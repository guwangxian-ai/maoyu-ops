"""Read-only reference check before cancelling a native Baidu share."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.services.baidu_drive_auth import BaiduDriveError
from common.db.session import async_session_maker
from common.models.card import Card
from common.models.cloud_asset import CloudAsset

ACTIVE = {"syncing", "sharing", "importing"}
_TEXT_LINK = re.compile(
    r"(?<![\w./@-])(?:https://)?pan\.baidu\.com(?::443)?/"
    r"(?:s/[A-Za-z0-9_-]+/?(?:[?#][^\s<>\"'\[\](){}，。；、）]*)?"
    r"|share/[A-Za-z0-9_/-]+\?[^\s<>\"'\[\](){}，。；、）]+)", re.I,
)


def _share_key(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, str) or len(value) > 16384 or any(c.isspace() or c == "\\" for c in value):
        return None
    try:
        url = urlsplit(value)
        if (url.scheme != "https" or url.hostname != "pan.baidu.com" or url.port not in {None, 443}
                or url.username or url.password):
            return None
        short = re.fullmatch(r"/s/([A-Za-z0-9_-]+)/?", url.path)
        if short:
            return ("short", short[1])
        if url.path not in {"/share/init", "/share/link"}:
            return None
        query = parse_qs(url.query, max_num_fields=40)
        surl = query.get("surl", [])
        if len(surl) == 1 and re.fullmatch(r"[A-Za-z0-9_-]+", surl[0]):
            # Baidu's password page /share/init?surl=X is /s/1X.
            return ("short", "1" + surl[0])
        share_id, uk = query.get("shareid", []), query.get("uk", [])
        if (len(share_id) == len(uk) == 1 and re.fullmatch(r"[0-9]{1,24}", share_id[0])
                and re.fullmatch(r"[0-9]{1,24}", uk[0])):
            return ("long", str(int(uk[0])), str(int(share_id[0])))
    except (ValueError, OverflowError):
        return None
    return None


def _targets(value: object) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise BaiduDriveError("资料关联记录无效，无法确认分享未被使用，已阻止取消")
    return value


def _text_references(text: object, share_key: tuple[str, ...]) -> bool:
    if text is None:
        return False
    if not isinstance(text, str):
        raise BaiduDriveError("卡券资料格式无效，无法确认分享未被使用，已阻止取消")
    for match in _TEXT_LINK.finditer(text):
        link = match[0].rstrip(".,;!?")
        if not link.lower().startswith("https://"):
            link = "https://" + link
        if _share_key(link) == share_key:
            return True
    return False


async def ensure_unreferenced(owner: int, share_url: str) -> None:
    """Fail closed if this owner's delivery data still depends on the share.

    Call while holding the existing owner-specific Baidu operation lock, before
    issuing share-cancel. This check never changes cards, assets or credentials.
    Disabled cards are checked too: enabling one later must not restore a link
    that the project itself knowingly invalidated.
    """
    if type(owner) is not int or owner <= 0:
        raise BaiduDriveError("当前用户无效，已阻止取消分享")
    key = _share_key(share_url)
    if key is None:
        raise BaiduDriveError("无法识别百度分享链接，已阻止取消，请刷新分享列表")
    try:
        async with async_session_maker() as session:
            assets = (await session.execute(select(CloudAsset.status, CloudAsset.targets, CloudAsset.published)
                .where(CloudAsset.owner_id == owner))).all()
            for asset in assets:
                targets = list(_targets(asset.targets))
                if asset.published is not None:
                    if not isinstance(asset.published, dict):
                        raise BaiduDriveError("资料发布快照无效，已阻止取消分享")
                    targets.extend(_targets(asset.published.get("targets")))
                baidu = [target for target in targets if target.get("provider") == "baidu"]
                if baidu and asset.status in ACTIVE:
                    raise BaiduDriveError("当前有百度网盘资料任务正在执行，请完成后再取消分享")
                if any(_share_key(target.get("share_url")) == key for target in baidu):
                    raise BaiduDriveError("此分享仍被同步资料或已发布版本引用，请先替换发货资料后再取消")
            cards = (await session.execute(select(Card.text_content, Card.description)
                .where(Card.user_id == owner, Card.type == "text"))).all()
            for card in cards:
                if _text_references(card.text_content, key) or _text_references(card.description, key):
                    raise BaiduDriveError("此分享仍被文字卡券引用，请先替换卡券中的发货资料后再取消")
    except SQLAlchemyError as exc:
        raise BaiduDriveError("检查分享与发货资料关联失败，已阻止取消，请恢复数据库连接后重试") from exc
