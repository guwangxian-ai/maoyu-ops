"""Owner checks at the MCP boundary for legacy handlers with global lookups.

The existing chat manager is indexed by account_id, and several legacy card
relation methods do not accept a user ID. Validate their references before
calling them; a non-admin principal alone cannot make those methods safe.
"""
from sqlalchemy import select

from common.db.session import async_session_maker
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.models.xy_account import XYAccount
from common.models.xy_catalog_item import XYCatalogItem


async def validate_references(spec, arguments, owner_id):
    module = spec.route.endpoint.__module__.rsplit(".", 1)[-1]
    fn = spec.route.endpoint.__name__
    path, query, body = (arguments.get(k) or {} for k in ("path", "query", "body"))
    if module in {"chat_new", "chat_new_image", "confirm_receipt_messages"}:
        account_id = path.get("account_id") or body.get("account_id")
        if account_id:
            async with async_session_maker() as session:
                owned = await session.scalar(select(XYAccount.id).where(
                    XYAccount.account_id == str(account_id), XYAccount.owner_id == owner_id))
                if owned is None:
                    raise ValueError("闲鱼账号不存在或不属于当前用户")
        return
    if module != "cards":
        return

    card_ids = {int(value) for value in (path.get("card_id"), body.get("card_id")) if value is not None}
    card_ids.update(map(int, body.get("card_ids") or []))
    if fn == "batch_delete_cards":
        card_ids.update(map(int, body.get("ids") or []))
    for relation in body.get("card_items") or []:
        if relation.get("source", "own") != "own" or relation.get("dock_record_id"):
            raise ValueError("MCP 卡券操作仅支持自有卡券；对接卡券关联请在页面管理")
        card_ids.add(int(relation["card_id"]))
    item_ids = {str(value) for value in (path.get("item_id"), query.get("item_id"), body.get("item_id")) if value}
    item_ids.update(map(str, body.get("item_ids") or []))
    if not card_ids and not item_ids:
        return
    async with async_session_maker() as session:
        if card_ids:
            owned = set((await session.scalars(select(Card.id).where(
                Card.id.in_(card_ids), Card.user_id == owner_id))).all())
            if owned != card_ids:
                raise ValueError("卡券不存在或不属于当前用户")
        if item_ids:
            owned = set((await session.scalars(select(XYCatalogItem.item_id).where(
                XYCatalogItem.item_id.in_(item_ids), XYCatalogItem.owner_id == owner_id))).all())
            if owned != item_ids:
                raise ValueError("商品不存在或不属于当前用户，请先同步本人商品")

        # These legacy handlers query/delete every relation for the selected
        # object. Do not read or change another owner's relation indirectly.
        relation_filter = None
        if fn in {"get_card_items", "update_card_items"}:
            relation_filter = CardItemRelation.card_id.in_(card_ids)
        elif fn == "delete_card_item_relation":
            relation_filter = CardItemRelation.card_id.in_(card_ids) & CardItemRelation.item_id.in_(item_ids)
        elif fn in {"get_cards_by_item", "update_item_cards", "batch_clear_item_relations"}:
            relation_filter = CardItemRelation.item_id.in_(item_ids)
        if relation_filter is not None:
            rows = (await session.execute(select(CardItemRelation.user_id, Card.user_id)
                .outerjoin(Card, Card.id == CardItemRelation.card_id).where(relation_filter))).all()
            if any(relation_owner != owner_id or card_owner != owner_id for relation_owner, card_owner in rows):
                raise ValueError("关联涉及其他用户或对接卡券，请在页面管理")
        if fn == "get_cards_by_item":
            legacy_other = await session.scalar(select(Card.id).where(
                Card.item_id.in_(item_ids), Card.user_id != owner_id).limit(1))
            if legacy_other is not None:
                raise ValueError("商品关联涉及其他用户卡券，MCP 不提供其内容")
