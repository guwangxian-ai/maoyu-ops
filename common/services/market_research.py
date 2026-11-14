"""Bounded market snapshots and evidence for on-demand Agent analysis.

Inspired by ai-goofish-monitor's snapshot-first workflow (MIT, 77f8d75).
Implemented against MaoYuOps' owned async database and existing MTOP clients.
No buyer actions, scheduled jobs or model calls are created here.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import timedelta
import hashlib
import json
import re
from statistics import median
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from common.models.market_observation import MarketMonitorState, MarketObservation, MarketSampleRun
from common.models.xy_account import XYAccount
from common.services.market_metrics import detail_metrics, exact_count, metric_change, price_number
from common.services.xianyu_detail_client import XianyuItemDetailClient
from common.services.xianyu_search_client import XianyuSearchClient, parse_search_item
from common.utils.time_utils import BEIJING_TZ, get_beijing_now_naive

RETENTION_DAYS = 90
MAX_SNAPSHOT_ITEMS = 300
METRICS = {"price": "price_source", "want_count": "want_source", "browse_count": "browse_source"}
ERROR_MESSAGES = {
    "login_required": "账号登录失效，请重新登录后再采样",
    "verification_required": "平台要求人工验证，请完成验证后再采样",
    "no_account": "没有可用的已启用采集账号",
    "parse_failed": "搜索结果结构无法确认，未把它记录成空市场",
    "request_failed": "采集请求失败，已有历史保留",
    "timeout": "本轮采样超时，已获取的样本标为部分结果",
    "detail_partial": "部分详情未取到，缺失指标保留未知",
    "interrupted": "采样中断；请先查看本批次，新的采样使用新编号",
    "storage_failed": "采样记录保存失败，未生成可靠观测",
}


def iso(value):
    return value.replace(tzinfo=BEIJING_TZ).isoformat(timespec="seconds") if value else None


def failure_code(error):
    text = str(error or "").lower()
    if any(word in text for word in ("parse", "结构", "解析")):
        return "parse_failed"
    if any(word in text for word in ("session_expired", "session过期", "登录失效", "login", "token_expired")):
        return "login_required"
    if any(word in text for word in ("validate", "验证", "captcha", "挤爆", "risk", "风控")):
        return "verification_required"
    return "request_failed"


def scope_key(keyword, source, filters):
    payload = json.dumps([keyword.strip(), source, filters], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def run_dict(run):
    status, code = run.status, run.error_code
    if status == "running" and run.started_at < get_beijing_now_naive() - timedelta(minutes=5):
        status, code = "interrupted", "interrupted"
    return {"id": run.id, "sample_key": run.sample_key, "scope_key": run.scope_key,
            "monitor_task_id": run.monitor_task_id, "keyword": run.keyword, "source": run.source,
            "filters": run.filters, "status": status, "pages_requested": run.pages_requested,
            "pages_completed": run.pages_completed, "item_count": run.item_count,
            "error_code": code, "message": ERROR_MESSAGES.get(code, run.message),
            "started_at": iso(run.started_at), "finished_at": iso(run.finished_at)}


def observation_dict(row):
    return {"id": row.id, "run_id": row.run_id, "scope_key": row.scope_key, "item_id": row.item_id,
            "title": row.title, "item_url": f"https://www.goofish.com/item?id={row.item_id}",
            "price": float(row.price) if row.price is not None else None, "price_source": row.price_source,
            "want_count": row.want_count, "want_source": row.want_source, "want_approximate": row.want_approximate,
            "browse_count": row.browse_count, "browse_source": row.browse_source,
            "tags": row.tags or [], "observed_at": iso(row.observed_at)}


async def finish_sample(session, run, items, *, status, pages_completed, error_code=None):
    """One atomic batch, deduped within a run. Partial data remains explicitly partial."""
    dedup = {str(item["item_id"]): item for item in items if re.fullmatch(r"\d{1,64}", str(item.get("item_id", "")))}
    if len(dedup) > MAX_SNAPSHOT_ITEMS:
        dedup = dict(list(dedup.items())[:MAX_SNAPSHOT_ITEMS])
        status = "partial"
        run.message = f"本轮历史最多保存 {MAX_SNAPSHOT_ITEMS} 件，超出范围未进入统计"
    if status == "failed":
        dedup = {}
    for item_id, item in dedup.items():
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = tags.split(",")
        want = exact_count(item.get("want_count"))
        browse = exact_count(item.get("browse_count"))
        session.add(MarketObservation(
            run_id=run.id, owner_id=run.owner_id, scope_key=run.scope_key, item_id=item_id,
            title=str(item.get("title") or "")[:500], price=price_number(item.get("price")),
            price_source=item.get("price_source") or "search.list_price",
            want_count=want, want_source=item.get("want_source") if want is not None else None,
            want_approximate=bool(item.get("want_approximate")), browse_count=browse,
            browse_source=item.get("browse_source") if browse is not None else None,
            tags=[str(tag)[:200] for tag in tags[:10]], observed_at=run.started_at))
    run.status = status
    run.pages_completed = pages_completed
    run.item_count = len(dedup)
    run.error_code = error_code
    run.finished_at = get_beijing_now_naive()
    await session.commit()
    return run_dict(run)


def new_sample(owner_id, keyword, source, filters, *, sample_key=None, task_id=None, pages=1):
    return MarketSampleRun(id=uuid4().hex, owner_id=owner_id, sample_key=sample_key or uuid4().hex,
                           scope_key=scope_key(keyword, source, filters), monitor_task_id=task_id,
                           keyword=keyword.strip(), source=source, filters=filters, status="running",
                           pages_requested=pages, pages_completed=0, item_count=0, started_at=get_beijing_now_naive())


async def collect_sample(session, owner_id, request):
    """At most 3 search pages + 10 sequential details, with a 90 second deadline."""
    filters = {key: request[key] for key in ("account_id", "start_page", "pages", "page_size", "detail_limit", "price_min", "price_max")}
    filters["sort"] = "default"
    filters["personal_only"] = True  # XianyuSearchClient's existing searchFilter
    key = scope_key(request["keyword"], "manual_search", filters)
    prior = await session.scalar(select(MarketSampleRun).where(
        MarketSampleRun.owner_id == owner_id, MarketSampleRun.sample_key == request["sample_key"]))
    if prior:
        if prior.scope_key != key:
            raise ValueError("该采样编号已用于其他参数；请先查看原批次")
        return {"run": run_dict(prior), "replayed": True}
    account = await session.scalar(select(XYAccount).where(
        XYAccount.owner_id == owner_id, XYAccount.account_id == request["account_id"],
        XYAccount.status == "active"))
    if not account or not account.cookie:
        raise ValueError(ERROR_MESSAGES["no_account"])
    run = new_sample(owner_id, request["keyword"], "manual_search", filters,
                     sample_key=request["sample_key"], pages=request["pages"])
    session.add(run)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        prior = await session.scalar(select(MarketSampleRun).where(
            MarketSampleRun.owner_id == owner_id, MarketSampleRun.sample_key == request["sample_key"]))
        if not prior or prior.scope_key != key:
            raise ValueError("采样编号冲突，请查看原批次")
        return {"run": run_dict(prior), "replayed": True}

    items, pages_completed, code = {}, 0, None
    client = XianyuSearchClient(account.account_id, account.cookie, owner_id=owner_id)
    try:
        async with asyncio.timeout(90):
            for page in range(request["start_page"], request["start_page"] + request["pages"]):
                result = await client.search(keyword=request["keyword"], page_number=page,
                                             rows_per_page=request["page_size"],
                                             price_min=request["price_min"], price_max=request["price_max"])
                if not result.get("success"):
                    code = failure_code(result.get("error"))
                    break
                entries = result.get("items")
                if not isinstance(entries, list):
                    code = "parse_failed"
                    break
                parsed = [p for entry in entries[:request["page_size"]] if (p := parse_search_item(entry))]
                if entries and not parsed:
                    code = "parse_failed"
                    break
                pages_completed += 1
                items.update({p["item_id"]: p for p in parsed})
                if not result.get("has_next_page"):
                    break
                await asyncio.sleep(0.3)
            if items and request["detail_limit"] and code not in {"login_required", "verification_required"}:
                detail_client = XianyuItemDetailClient(account.account_id, client.cookies_str, owner_id=owner_id)
                for item in list(items.values())[:request["detail_limit"]]:
                    result = await detail_client.get_detail(item["item_id"])
                    if result.get("success"):
                        item.update(detail_metrics(result))
                    else:
                        code = code or (failure_code(result.get("error")) if result.get("account_invalid") else "detail_partial")
                        if result.get("account_invalid"):
                            break
                    await asyncio.sleep(0.2)
    except TimeoutError:
        code = "timeout"
    except Exception:
        # Never persist raw upstream errors that can contain tokens, cookies or verification URLs.
        code = "request_failed"
    status = ("partial" if pages_completed else "failed") if code else "success"
    result = await finish_sample(session, run, list(items.values()), status=status,
                                 pages_completed=pages_completed, error_code=code)
    return {"run": result, "replayed": False}


class MarketResearchService:
    def __init__(self, session, owner_id):
        self.session, self.owner_id = session, owner_id

    async def runs(self, *, keyword=None, days=30, page=1, page_size=20):
        conditions = [MarketSampleRun.owner_id == self.owner_id,
                      MarketSampleRun.started_at >= get_beijing_now_naive() - timedelta(days=days)]
        if keyword:
            conditions.append(MarketSampleRun.keyword == keyword.strip())
        count = await self.session.scalar(select(func.count()).select_from(MarketSampleRun).where(*conditions))
        rows = (await self.session.scalars(select(MarketSampleRun).where(*conditions)
                .order_by(MarketSampleRun.started_at.desc(), MarketSampleRun.id.desc())
                .offset((page-1)*page_size).limit(page_size))).all()
        return {"list": [run_dict(row) for row in rows], "total": count, "page": page, "page_size": page_size,
                "retention_days": RETENTION_DAYS}

    async def history(self, item_id, *, scope=None, days=30, page=1, page_size=50):
        conditions = [MarketObservation.owner_id == self.owner_id, MarketObservation.item_id == item_id,
                      MarketObservation.observed_at >= get_beijing_now_naive() - timedelta(days=days)]
        if scope:
            conditions.append(MarketObservation.scope_key == scope)
        count = await self.session.scalar(select(func.count()).select_from(MarketObservation).where(*conditions))
        rows = (await self.session.scalars(select(MarketObservation).where(*conditions)
                .order_by(MarketObservation.observed_at.desc(), MarketObservation.id.desc())
                .offset((page-1)*page_size).limit(page_size))).all()
        return {"list": [observation_dict(row) for row in rows], "total": count, "page": page,
                "page_size": page_size, "timezone": "Asia/Shanghai", "scope_key": scope,
                "notice": "不同采样范围/字段来源不合并计算；未返回不代表售出或下架。"}

    async def candidates(self, scope, *, days=30, limit=50, include_terms=None, exclude_terms=None):
        """Latest bounded batch + SQL-selected endpoints, not unbounded history loading."""
        cutoff = get_beijing_now_naive() - timedelta(days=days)
        base = [MarketSampleRun.owner_id == self.owner_id, MarketSampleRun.scope_key == scope,
                MarketSampleRun.started_at >= cutoff]
        latest_attempt = await self.session.scalar(select(MarketSampleRun).where(*base)
                                                   .order_by(MarketSampleRun.started_at.desc()).limit(1))
        latest = await self.session.scalar(select(MarketSampleRun).where(*base,
            MarketSampleRun.status.in_(["success", "partial"]))
            .order_by(MarketSampleRun.started_at.desc()).limit(1))
        if not latest:
            return {"list": [], "run": None, "latest_attempt": run_dict(latest_attempt) if latest_attempt else None,
                    "summary": {"sample_items": 0}, "analysis_status": "no_samples"}
        current = (await self.session.scalars(select(MarketObservation).where(
            MarketObservation.owner_id == self.owner_id, MarketObservation.run_id == latest.id)
            .order_by(MarketObservation.item_id).limit(MAX_SNAPSHOT_ITEMS))).all()
        ids = [row.item_id for row in current]
        conditions = [MarketObservation.owner_id == self.owner_id, MarketObservation.scope_key == scope,
                      MarketObservation.observed_at >= cutoff, MarketObservation.item_id.in_(ids)]
        # Window selection returns at most two endpoints per item, regardless of run frequency.
        ranked = select(MarketObservation.id,
            func.row_number().over(partition_by=MarketObservation.item_id,
                order_by=(MarketObservation.observed_at.asc(), MarketObservation.id.asc())).label("rn_first"),
            func.row_number().over(partition_by=MarketObservation.item_id,
                order_by=(MarketObservation.observed_at.desc(), MarketObservation.id.desc())).label("rn_last")
        ).where(*conditions).subquery()
        endpoints = (await self.session.scalars(select(MarketObservation).join(ranked, ranked.c.id == MarketObservation.id)
            .where((ranked.c.rn_first == 1) | (ranked.c.rn_last == 1))
            .order_by(MarketObservation.observed_at, MarketObservation.id))).all() if ids else []
        grouped = defaultdict(list)
        for row in endpoints:
            grouped[row.item_id].append(observation_dict(row))
        aggregates = (await self.session.execute(select(MarketObservation.item_id, func.count(),
            *[func.count(func.distinct(getattr(MarketObservation, source))) for source in METRICS.values()])
            .where(*conditions).group_by(MarketObservation.item_id))).all() if ids else []
        stats = {row[0]: row[1:] for row in aggregates}
        output = []
        for row in current:
            data = observation_dict(row)
            history = grouped[row.item_id]
            count, *source_counts = stats[row.item_id]
            changes = {metric: metric_change(history[0], history[-1], metric, source,
                       sample_count=count, mixed_sources=source_counts[index] > 1)
                       for index, (metric, source) in enumerate(METRICS.items())}
            title = (row.title or "").casefold()
            matched = [term for term in (include_terms or []) if keyword_matches(term, title)]
            excluded = [term for term in (exclude_terms or []) if keyword_matches(term, title)]
            if excluded or (include_terms and not matched):
                decision = "filtered_out"
            elif changes["want_count"]["value"] is not None and changes["want_count"]["value"] > 0:
                decision = "observed_growth"
            else:
                decision = "needs_review"
            output.append({**data, "sample_count": count, "first_observed_at": history[0]["observed_at"],
                "changes": changes, "evidence_ids": [history[0]["id"], history[-1]["id"]],
                "candidate_signal": decision, "matched_terms": matched, "excluded_terms": excluded,
                "missing_evidence": ["actual_sales", "redistribution_rights", "delivery_resources"],
                "analysis_status": "agent_review_required"})
        prices = [float(row.price) for row in current if row.price is not None]
        output.sort(key=lambda item: (item["candidate_signal"] == "filtered_out",
                                      -(item["changes"]["want_count"]["value"] or 0), item["item_id"]))
        return {"list": output[:limit], "run": run_dict(latest),
                "latest_attempt": run_dict(latest_attempt), "days": days,
                "summary": {"sample_items": len(current), "returned_items": min(limit, len(output)),
                    "known_want_items": sum(row.want_count is not None for row in current),
                    "price_samples": len(prices), "median_asking_price": median(prices) if prices else None,
                    "min_asking_price": min(prices) if prices else None, "max_asking_price": max(prices) if prices else None},
                "analysis_status": "agent_review_required", "model_calls": 0,
                "notice": "这是抽样挂牌价和想要数净变化，不是成交、销量或收益预测；跨来源/取整计数不计算增长。"}

    async def monitor_states(self, page=1, page_size=20):
        from common.models.listing_monitor_task import ListingMonitorTask
        stmt = select(MarketMonitorState, ListingMonitorTask.keyword, ListingMonitorTask.is_enabled).join(
            ListingMonitorTask, ListingMonitorTask.id == MarketMonitorState.task_id).where(
            MarketMonitorState.owner_id == self.owner_id, ListingMonitorTask.owner_id == self.owner_id,
            ListingMonitorTask.is_deleted.is_(False))
        count = await self.session.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = (await self.session.execute(stmt.order_by(MarketMonitorState.last_checked_at.desc())
                 .offset((page-1)*page_size).limit(page_size))).all()
        return {"list": [{"task_id": s.task_id, "keyword": keyword, "is_enabled": enabled,
            "status": s.status, "consecutive_failures": s.consecutive_failures, "error_code": s.error_code,
            "message": ERROR_MESSAGES.get(s.error_code), "paused_until": iso(s.paused_until),
            "last_checked_at": iso(s.last_checked_at), "last_success_at": iso(s.last_success_at),
            "last_run_id": s.last_run_id} for s, keyword, enabled in rows],
            "total": count, "page": page, "page_size": page_size}


def keyword_matches(term, title):
    term = term.strip().casefold()
    if re.fullmatch(r"[a-z0-9]+", term):
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", title))
    return bool(term) and term in title


async def update_monitor_state(session, task, run, *, collection_status=None):
    state = await session.get(MarketMonitorState, task.id)
    if state is None:
        state = MarketMonitorState(task_id=task.id, owner_id=task.owner_id, consecutive_failures=0)
        session.add(state)
    state.last_checked_at, state.last_run_id = get_beijing_now_naive(), run.id
    state.error_code = run.error_code
    if (collection_status or run.status) == "success":
        state.consecutive_failures = 0
        state.paused_until = None
        state.status = "ready"
        state.last_success_at = state.last_checked_at
    else:
        state.consecutive_failures += 1
        state.status = "degraded"
        if state.consecutive_failures >= 3:
            minutes = min(240, 15 * (2 ** min(4, state.consecutive_failures - 3)))
            state.paused_until = state.last_checked_at + timedelta(minutes=minutes)
            state.status = "paused"
    await session.commit()


async def cleanup_market_history(session, limit=2000):
    """Bounded retention for these new tables only. Never delete operational data."""
    cutoff = get_beijing_now_naive() - timedelta(days=RETENTION_DAYS)
    ids = list((await session.scalars(select(MarketObservation.id).where(MarketObservation.observed_at < cutoff)
                                    .order_by(MarketObservation.observed_at).limit(limit))).all())
    if ids:
        await session.execute(delete(MarketObservation).where(MarketObservation.id.in_(ids)))
    run_ids = list((await session.scalars(select(MarketSampleRun.id).where(MarketSampleRun.started_at < cutoff,
        ~select(MarketObservation.id).where(MarketObservation.run_id == MarketSampleRun.id).exists())
        .limit(limit))).all())
    if run_ids:
        await session.execute(delete(MarketSampleRun).where(MarketSampleRun.id.in_(run_ids)))
    await session.commit()
    return {"observations": len(ids), "runs": len(run_ids)}
