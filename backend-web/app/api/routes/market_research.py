"""Owned market evidence for the UI and MCP. No publishing or buyer actions."""
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user, get_db_session
from common.models.user import User
from common.schemas.common import ApiResponse
from common.services.market_research import MarketResearchService, collect_sample

router = APIRouter(prefix="/market-research", tags=["竞品研究"])
_sample_lock = asyncio.Lock()  # Single backend admission control: no waiting queue or browser storm.


class MarketSampleRequest(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=200, description="关键词；同范围采样才比较历史")
    account_id: str = Field(..., min_length=1, max_length=80, description="本人已启用的闲鱼账号标识，不是数据库 pk")
    sample_key: str = Field(..., pattern=r"^[A-Za-z0-9:_-]{8,80}$", description="本轮唯一采样编号；重试沿用原编号，返回原批次，不重复请求")
    start_page: int = Field(1, ge=1, le=10)
    pages: int = Field(1, ge=1, le=3)
    page_size: int = Field(20, ge=1, le=30)
    detail_limit: int = Field(0, ge=0, le=10, description="按需补充详情人数和浏览量，最多10件；未知不冒充0")
    price_min: Optional[float] = Field(None, ge=0, le=9999999999, allow_inf_nan=False)
    price_max: Optional[float] = Field(None, ge=0, le=9999999999, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_range(self):
        self.keyword = self.keyword.strip()
        if not self.keyword:
            raise ValueError("关键词不能为空")
        if self.price_min is not None and self.price_max is not None and self.price_min > self.price_max:
            raise ValueError("最低价不能高于最高价")
        return self


@router.post("/samples", response_model=ApiResponse)
async def collect_market_sample(
    request: MarketSampleRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
):
    """按次读取平台并保存本人市场样本，最多3页/10详情/90秒；不创建持续任务、不下单、不私信。

    先保存 sample_key 回执；断线用相同编号恢复。success/partial/failed 必须查看 data.run.status。
    """
    if _sample_lock.locked():
        return ApiResponse(success=False, code=409, message="已有按次采样正在执行，请先查询批次，稍后沿用原编号重试")
    async with _sample_lock:
        try:
            data = await collect_sample(session, current_user.id, request.model_dump())
        except ValueError as exc:
            return ApiResponse(success=False, message=str(exc))
    status = data["run"]["status"]
    return ApiResponse(success=status in {"success", "partial"},
                       message=data["run"].get("message") or f"采样状态：{status}", data=data)


@router.get("/samples", response_model=ApiResponse)
async def list_market_samples(
    keyword: Optional[str] = Query(None, max_length=200),
    days: int = Query(30, ge=1, le=90), page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_active_user), session: AsyncSession = Depends(get_db_session),
):
    """查询本人竞品采样批次与完整性，返回 scope_key 供候选/趋势查询。不是本人在售商品。"""
    data = await MarketResearchService(session, current_user.id).runs(keyword=keyword, days=days, page=page, page_size=page_size)
    return ApiResponse(success=True, data=data)


@router.get("/history/{item_id}", response_model=ApiResponse)
async def get_market_history(
    item_id: str, scope_key: Optional[str] = Query(None, pattern=r"^[a-f0-9]{64}$"),
    days: int = Query(30, ge=1, le=90), page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_active_user), session: AsyncSession = Depends(get_db_session),
):
    """读取本人保存的同商品历史；包含价格/想要/浏览的来源、时间与采样范围。"""
    data = await MarketResearchService(session, current_user.id).history(
        item_id, scope=scope_key, days=days, page=page, page_size=page_size)
    return ApiResponse(success=True, data=data)


@router.get("/candidates", response_model=ApiResponse)
async def get_market_candidates(
    scope_key: str = Query(..., pattern=r"^[a-f0-9]{64}$"), days: int = Query(30, ge=1, le=90),
    limit: int = Query(50, ge=1, le=100),
    include_terms: Optional[str] = Query(None, max_length=300, description="逗号分隔，最多10个任意匹配词"),
    exclude_terms: Optional[str] = Query(None, max_length=300, description="逗号分隔，最多10个排除词"),
    current_user: User = Depends(get_current_active_user), session: AsyncSession = Depends(get_db_session),
):
    """同范围最新样本、价格摘要及可比净变化，供 Agent 选品；不是AI结论、销量或收益保证。

    明确列出推荐依据、缺失数据和最新采集失败。后台模型调用为0，许可和交付能力另行核验。
    """
    def terms(value):
        return list(dict.fromkeys(term.strip() for term in (value or "").split(",") if term.strip()))[:10]
    data = await MarketResearchService(session, current_user.id).candidates(
        scope_key, days=days, limit=limit, include_terms=terms(include_terms), exclude_terms=terms(exclude_terms))
    return ApiResponse(success=True, data=data)


@router.get("/monitor-states", response_model=ApiResponse)
async def list_market_monitor_states(
    page: int = Query(1, ge=1, le=1000), page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_active_user), session: AsyncSession = Depends(get_db_session),
):
    """本人既有监控的持久失败/暂停状态；扫码后等待冷却试采成功才恢复，不自动启用停用账号。"""
    return ApiResponse(success=True, data=await MarketResearchService(session, current_user.id).monitor_states(page, page_size))
