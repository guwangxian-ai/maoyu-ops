"""Persistent multi-provider asset workflow built on existing MySQL jobs and CLIs."""
from __future__ import annotations

import asyncio
import copy
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select, update

from app.services import cloud_asset_providers as providers
from app.services import cloud_asset_storage as storage
from app.services.cloud_asset_storage import AssetError
from common.db.session import async_session_maker
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.models.cloud_asset import CloudAsset
from common.models.cloud_drive_job import CloudDriveJob
from common.models.xy_catalog_item import XYCatalogItem

ACTIVE = ("syncing", "sharing", "importing")
_running: dict[str, asyncio.Task] = {}
_gate = asyncio.Semaphore(2)


def name(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 100 or any(ord(c) < 32 for c in value):
        raise AssetError("资料名称须为 1 到 100 个字符")
    return value


def _targets(owner: int, selected: list[str]) -> list[dict]:
    if not selected or len(selected) > 2 or len(set(selected)) != len(selected) or any(p not in providers.PROVIDERS for p in selected):
        raise AssetError("请选择不同的受支持网盘")
    result = []
    for provider in selected:
        account = providers.account_id(owner, provider)
        if not account:
            raise AssetError(f"请先授权{providers.LABELS[provider]}")
        result.append({"provider": provider, "account_id": account, "status": "pending", "files": [], "message": "等待同步"})
    return result


async def _get(session, owner: int, asset_id: str, *, lock=False) -> CloudAsset:
    statement = select(CloudAsset).where(CloudAsset.id == asset_id, CloudAsset.owner_id == owner)
    if lock:
        statement = statement.with_for_update()
    asset = await session.scalar(statement)
    if asset is None:
        raise AssetError("资料不存在或无权访问")
    return asset


def view(asset: CloudAsset, *, detailed=True) -> dict:
    from common.services.cloud_asset_delivery import CloudAssetDeliveryError, render_cloud_asset_delivery
    published = asset.published or {}
    content, deliverable = "", False
    if published:
        try:
            content = render_cloud_asset_delivery(published)
            deliverable = True
        except CloudAssetDeliveryError:
            pass
    result = {"id": asset.id, "name": asset.name, "version": asset.version, "status": asset.status,
              "message": asset.message, "files": asset.manifest,
              "targets": [{key: copy.deepcopy(value) for key, value in target.items() if key not in {"account_id", "last_job_id"}} for target in asset.targets],
              "card_id": str(asset.card_id) if asset.card_id else None,
              "delivery_content": content, "published_version": published.get("version"), "deliverable": deliverable,
              "created_at": asset.created_at.isoformat(), "updated_at": asset.updated_at.isoformat()}
    if detailed:
        result["analysis"] = asset.analysis
    return result


async def get(owner: int, asset_id: str) -> dict:
    async with async_session_maker() as session:
        return view(await _get(session, owner, asset_id))


async def listing(owner: int, page=1, page_size=20) -> dict:
    async with async_session_maker() as session:
        total = await session.scalar(select(func.count()).select_from(CloudAsset).where(CloudAsset.owner_id == owner))
        records = (await session.scalars(select(CloudAsset).where(CloudAsset.owner_id == owner)
            .order_by(CloudAsset.created_at.desc(), CloudAsset.id.desc()).offset((page-1)*page_size).limit(page_size))).all()
        return {"list": [view(record, detailed=False) for record in records], "total": total, "page": page, "page_size": page_size}


async def upload(owner: int, label: str, selected: list[str], files) -> dict:
    label, targets, identifier = name(label), _targets(owner, selected), uuid4().hex
    try:
        manifest = await storage.stage_uploads(owner, identifier, 1, files)
        async with async_session_maker() as session:
            asset = CloudAsset(id=identifier, owner_id=owner, name=label, version=1, status="staged",
                               manifest=manifest, targets=targets, message="资料已保存，可同步到所选网盘")
            session.add(asset)
            await session.commit()
    except BaseException:
        shutil.rmtree(storage.directory(owner, identifier, 1).parent, ignore_errors=True)
        raise
    return await get(owner, identifier)


async def replace_version(owner: int, identifier: str, files) -> dict:
    # Lock serializes staging a replacement against sync/share and other updates.
    async with async_session_maker() as session:
        asset = await _get(session, owner, identifier, lock=True)
        if asset.status in ACTIVE:
            raise AssetError("当前任务正在运行，请完成后更新版本")
        next_version = asset.version + 1
        try:
            manifest = await storage.stage_uploads(owner, identifier, next_version, files)
        except BaseException:
            shutil.rmtree(storage.directory(owner, identifier, next_version), ignore_errors=True)
            raise
        asset.version, asset.manifest, asset.analysis = next_version, manifest, None
        asset.targets = [{"provider": row["provider"], "account_id": row["account_id"], "status": "pending", "files": [], "message": "新版本等待同步"} for row in asset.targets]
        asset.status, asset.message = "staged", "新版本已保存，等待同步与分享。" + ("已有卡券继续使用上一发布版本。" if asset.published else "全部指定平台分享成功后发布。")
        await session.commit()
    return await get(owner, identifier)


async def rename(owner: int, identifier: str, label: str) -> dict:
    async with async_session_maker() as session:
        asset = await _get(session, owner, identifier, lock=True)
        if asset.status in ACTIVE:
            raise AssetError("当前任务正在执行，请完成后修改资料名称")
        asset.name = name(label)
        await session.commit()
    return await get(owner, identifier)


def _claim_matches(asset: CloudAsset, version: int, job_id: str) -> bool:
    return asset.version == version and asset.status in ACTIVE and bool(asset.targets) and all(
        target.get("last_job_id") == job_id for target in asset.targets)


async def _change(owner: int, identifier: str, provider: str, values: dict, *, version: int, job_id: str) -> None:
    async with async_session_maker() as session:
        asset = await _get(session, owner, identifier, lock=True)
        if not _claim_matches(asset, version, job_id):
            raise AssetError("任务已失效，停止保存旧版本结果")
        targets = copy.deepcopy(asset.targets)
        next(target for target in targets if target["provider"] == provider).update(copy.deepcopy(values))
        asset.targets = targets
        await session.commit()


async def enqueue(owner: int, identifier: str, operation: str, params: dict | None = None, *, expected_version: int | None = None) -> dict:
    async with async_session_maker() as session:
        asset = await _get(session, owner, identifier, lock=True)
        if asset.status in ACTIVE:
            raise AssetError("当前资料已有任务执行中，请查看进度")
        if expected_version is not None and expected_version != asset.version:
            raise AssetError("资料版本已更新，请刷新后操作")
        if operation == "sync" and not asset.manifest:
            raise AssetError("请先完成资料导入")
        if operation == "sync" and any(t.get("share_outcome_unknown") or
                t.get("status") == "unknown" and t.get("last_operation") == "shares" for t in asset.targets):
            raise AssetError("存在未确认的分享结果，请先恢复分享并核验，停止重新同步")
        if operation == "shares" and (not asset.manifest or any(not t.get("files") or t["status"] not in {"uploaded", "shared", "failed", "unknown"} for t in asset.targets)):
            raise AssetError("请先完成所有指定平台的文件同步")
        for target in asset.targets:
            providers.binding(owner, target)
            if operation == "shares" and len(target.get("files", [])) != len(asset.manifest):
                raise AssetError("副本文件不完整，请先同步")
        job_id = uuid4().hex
        job = CloudDriveJob(id=job_id, owner_id=owner, provider="multi", operation=operation, status="queued",
                           params={"asset_id": identifier, "version": asset.version, **(params or {})}, message="等待处理")
        session.add(job)
        asset.targets = [{**target, "last_job_id": job_id} for target in copy.deepcopy(asset.targets)]
        asset.status = {"sync": "syncing", "shares": "sharing", "import": "importing", "url": "importing"}[operation]
        asset.message = "后台任务已提交，可刷新查看真实进度"
        await session.commit()
    task = asyncio.create_task(_run(owner, identifier, job_id), name=f"cloud-asset-{job_id}")
    _running[job_id] = task
    task.add_done_callback(lambda finished: _running.pop(job_id, None))
    return await get(owner, identifier)


async def import_source(owner: int, label: str, selected: list[str] | None, source: dict) -> dict:
    if not selected:
        selected = [provider for provider in providers.PROVIDERS if providers.account_id(owner, provider)]
    targets, identifier = _targets(owner, selected), uuid4().hex
    if source.get("provider"):
        if not source.get("file_ids") and not source.get("paths") and not source.get("url"):
            raise AssetError("请选择来源网盘文件")
        if source.get("url"):
            providers.validate_share_source(source)
        elif source["provider"] == "quark" and (not source.get("file_ids") or source.get("paths")) or source["provider"] == "baidu" and (not source.get("paths") or source.get("file_ids")):
            raise AssetError("夸克文件导入使用 file_ids，百度文件导入使用 paths")
        source = {**source, "account_id": providers.account_id(owner, source["provider"])}
        providers.binding(owner, source)
    else:
        storage.public_url(source["url"])
        storage.filename(source["filename"])
    async with async_session_maker() as session:
        session.add(CloudAsset(id=identifier, owner_id=owner, name=name(label), version=1, status="staged",
                              manifest=[], targets=targets, message="等待导入"))
        await session.commit()
    return await enqueue(owner, identifier, "import" if source.get("provider") else "url", {"source": source})


async def retry(owner: int, identifier: str) -> dict:
    async with async_session_maker() as session:
        asset = await _get(session, owner, identifier)
        latest = {target.get("last_job_id") for target in asset.targets}
        job = await session.get(CloudDriveJob, next(iter(latest))) if len(latest) == 1 and None not in latest else None
        if asset.status not in {"failed", "partial", "interrupted"} or job is None or job.owner_id != owner or job.provider != "multi" or job.params.get("asset_id") != identifier or job.params.get("version") != asset.version:
            raise AssetError("没有可恢复的任务")
        if job.operation in {"import", "url"} and not asset.manifest:
            raise AssetError("来源导入未完成，请重新导入；不会重复覆盖未确认的下载结果")
        params = dict(job.params)
        params.pop("asset_id", None)
        params.pop("version", None)
        version, operation = asset.version, job.operation
    return await enqueue(owner, identifier, operation, params, expected_version=version)


async def _finish(owner: int, identifier: str, job_id: str, operation: str, version: int) -> None:
    from common.services.cloud_asset_delivery import render_cloud_asset_delivery
    async with async_session_maker() as session:
        asset = await _get(session, owner, identifier, lock=True)
        if not _claim_matches(asset, version, job_id):
            raise AssetError("任务已失效，停止发布旧版本结果")
        if operation in {"import", "url"}:
            asset.status, asset.message = "staged", "来源文件已下载并校验，可同步到所选网盘"
        else:
            required = "shared" if operation == "shares" else {"uploaded", "shared"}
            good = [target for target in asset.targets if target["status"] == required] if isinstance(required, str) else [target for target in asset.targets if target["status"] in required]
            complete = len(good) == len(asset.targets)
            asset.status = ("deliverable" if operation == "shares" else "ready") if complete else "partial" if good else "failed"
            asset.message = f"{'分享' if operation == 'shares' else '同步'}完成 {len(good)}/{len(asset.targets)} 个平台"
            if complete and operation == "shares":
                snapshot = {"name": asset.name, "version": asset.version,
                            "required_providers": [target["provider"] for target in asset.targets], "targets": copy.deepcopy(asset.targets)}
                snapshot["delivery_content"] = render_cloud_asset_delivery(snapshot)
                asset.published = snapshot
                if asset.card_id:
                    card = await session.scalar(select(Card).where(Card.id == asset.card_id, Card.user_id == owner).with_for_update())
                    if card is None or card.type != "text":
                        raise AssetError("关联卡券不存在或类型已变化，停止发布")
                    card.text_content = snapshot["delivery_content"]
        await session.execute(update(CloudDriveJob).where(CloudDriveJob.id == job_id).values(
            status="succeeded" if asset.status in {"staged", "ready", "deliverable"} else "partial" if asset.status == "partial" else "failed",
            message=asset.message, result={"asset_id": identifier, "version": asset.version, "status": asset.status}))
        await session.commit()


async def _run(owner: int, identifier: str, job_id: str) -> None:
    version = None
    try:
        async with _gate:
            async with async_session_maker() as session:
                asset = await _get(session, owner, identifier)
                job = await session.get(CloudDriveJob, job_id)
                operation, params = job.operation, copy.deepcopy(job.params)
                version = params["version"]
                if not _claim_matches(asset, version, job_id):
                    raise AssetError("任务版本已变化，停止执行")
                base = storage.directory(owner, identifier, asset.version)
                manifest, targets, label = copy.deepcopy(asset.manifest), copy.deepcopy(asset.targets), asset.name
                await session.execute(update(CloudDriveJob).where(CloudDriveJob.id == job_id).values(status="running"))
                await session.commit()
            if operation in {"import", "url"}:
                source = params["source"]
                if source.get("provider") and source.get("url"):
                    source = await providers.transfer_source(owner, identifier, source)
                manifest = await providers.import_files(owner, source, base, source.get("file_ids", []), source.get("paths", [])) if operation == "import" else [await storage.download_url(source["url"], base / storage.filename(source["filename"]))]
                async with async_session_maker() as session:
                    asset = await _get(session, owner, identifier, lock=True)
                    if not _claim_matches(asset, version, job_id):
                        raise AssetError("导入任务已失效")
                    asset.manifest = manifest
                    await session.commit()
            else:
                async def one(target):
                    provider = target["provider"]
                    async def checkpoint(values):
                        await _change(owner, identifier, provider, values, version=version, job_id=job_id)
                    try:
                        providers.binding(owner, target)
                        if operation == "sync":
                            # Reconcile exact version directory even for previously uploaded replicas.
                            await checkpoint({"status": "uploading", "message": "核对并上传资料副本", "last_operation": "sync"})
                            result = await providers.upload(owner, identifier, params["version"], base, manifest, target, checkpoint)
                            await checkpoint({**result, "status": "shared" if target.get("share_url") else "uploaded", "message": "文件同步已核验"})
                        else:
                            await checkpoint({"status": "sharing", "message": "创建或复用实际分享", "last_operation": "shares"})
                            result = await providers.share(owner, label, target, params["period"])
                            await checkpoint({**result, "status": "shared", "share_outcome_unknown": False, "message": "分享链接已保存"})
                    except (AssetError, providers.qa.QuarkAuthError, providers.ba.BaiduDriveError, OSError) as exc:
                        message = str(exc) if not isinstance(exc, OSError) else "本地资料读取失败"
                        message = message.replace(str(storage.root()), "[私有资料目录]")[:300]
                        uncertain = isinstance(exc, providers.qa.QuarkTimeoutError) or (operation == "shares" and (target.get("share_outcome_unknown") or target.get("status") == "unknown" or not isinstance(exc, AssetError)))
                        await checkpoint({"status": "unknown" if uncertain else "failed", "last_operation": operation,
                                          "share_outcome_unknown": uncertain and operation == "shares", "message": message})
                # Unexpected failures cancel and await siblings before releasing the
                # asset claim, so old operations cannot write into a replacement.
                async with asyncio.TaskGroup() as group:
                    for target in targets:
                        group.create_task(one(target))
            await _finish(owner, identifier, job_id, operation, version)
    except BaseException as exc:
        interrupted = isinstance(exc, asyncio.CancelledError)
        async with async_session_maker() as session:
            asset = await _get(session, owner, identifier, lock=True)
            if version is None or not _claim_matches(asset, version, job_id):
                await session.execute(update(CloudDriveJob).where(CloudDriveJob.id == job_id).values(status="interrupted", message="任务已失效，旧结果未写入资料"))
                await session.commit()
                if interrupted:
                    raise
                return
            asset.status = "interrupted" if interrupted else "failed"
            asset.message = "服务停止，需核验副本后恢复" if interrupted else str(exc)[:300] if isinstance(exc, AssetError) else "资料任务失败，请查看平台结果并重试"
            targets = copy.deepcopy(asset.targets)
            for target in targets:
                if target["status"] in {"uploading", "sharing"}:
                    if target["status"] == "sharing":
                        target["share_outcome_unknown"] = True
                    target["status"] = "unknown"
                    target["message"] = "请求已中断，需核验已提交结果后恢复"
            asset.targets = targets
            await session.execute(update(CloudDriveJob).where(CloudDriveJob.id == job_id).values(status=asset.status, message=asset.message))
            await session.commit()
            if not asset.manifest and not asset.published:
                shutil.rmtree(storage.directory(owner, identifier, asset.version), ignore_errors=True)
        if interrupted:
            raise


async def analyze(owner: int, identifier: str) -> dict:
    async with async_session_maker() as session:
        asset = await _get(session, owner, identifier, lock=True)
        if asset.status in ACTIVE or not asset.manifest:
            raise AssetError("请先完成来源导入或当前任务")
        asset.analysis = await asyncio.to_thread(storage.analyze_files, storage.directory(owner, identifier, asset.version), asset.manifest)
        await session.commit()
    return await get(owner, identifier)


async def create_card(owner: int, identifier: str, item_ids: list[str]) -> dict:
    from common.services.cloud_asset_delivery import CloudAssetDeliveryError, render_cloud_asset_delivery
    async with async_session_maker() as session:
        asset = await _get(session, owner, identifier, lock=True)
        if not asset.published:
            raise AssetError("全部指定网盘分享完成后才能生成发货卡券")
        try:
            content = render_cloud_asset_delivery(asset.published)
        except CloudAssetDeliveryError as exc:
            raise AssetError(str(exc)) from exc
        selected = list(dict.fromkeys(item_ids))
        if selected:
            owned = (await session.scalars(select(XYCatalogItem).where(XYCatalogItem.owner_id == owner, XYCatalogItem.item_id.in_(selected)))).all()
            if {str(item.item_id) for item in owned} != set(selected):
                raise AssetError("关联商品不存在或不属于当前用户")
        if not asset.card_id:
            card = Card(user_id=owner, name=asset.name, type="text", enabled=True, text_content=content,
                        description="{DELIVERY_CONTENT}")
            session.add(card)
            await session.flush()
            asset.card_id = card.id
        else:
            card = await session.scalar(select(Card).where(Card.id == asset.card_id, Card.user_id == owner).with_for_update())
            if card is None or card.type != "text":
                raise AssetError("关联卡券已删除或类型已变化")
            card.text_content = content
        existing = set((await session.scalars(select(CardItemRelation.item_id).where(CardItemRelation.card_id == card.id))).all())
        for item_id in selected:
            if item_id not in existing:
                session.add(CardItemRelation(user_id=owner, card_id=card.id, item_id=item_id, source="own"))
        await session.commit()
    return await get(owner, identifier)


async def recover() -> None:
    async with async_session_maker() as session:
        stale = (await session.scalars(select(CloudAsset).where(CloudAsset.status.in_(ACTIVE)).with_for_update())).all()
        for asset in stale:
            targets = copy.deepcopy(asset.targets)
            for target in targets:
                if target["status"] in {"uploading", "sharing"}:
                    if target["status"] == "sharing":
                        target["share_outcome_unknown"] = True
                    target["status"] = "unknown"
                    target["message"] = "服务重启，先核验已提交结果，分享记录缺失时停止重建"
            asset.targets = targets
            asset.status, asset.message = "interrupted", "服务重启，资料副本结果需核验后恢复；上一发布版本保留"
        await session.execute(update(CloudDriveJob).where(CloudDriveJob.provider == "multi", CloudDriveJob.status.in_(("queued", "running"))).values(status="interrupted", message="服务重启，需核验后恢复"))
        await session.commit()


async def shutdown() -> None:
    pending = list(_running.values())
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
