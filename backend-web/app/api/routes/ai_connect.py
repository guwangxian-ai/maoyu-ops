"""Public onboarding only. No business data or credentials are served here."""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from app.mcp import onboarding

router = APIRouter(prefix="/ai", tags=["AI 公开接入说明"])
HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-cache"}


def unavailable():
    # Explicit response: the legacy application's exception handler normalizes
    # HTTPException to status 200, which is unsuitable for public downloads.
    return PlainTextResponse("接入资源暂不可用，请联系管理员检查服务地址与技能包部署。", status_code=503, headers=HEADERS)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing():
    try:
        content = onboarding.landing()
    except (ValueError, FileNotFoundError):
        return unavailable()
    return HTMLResponse(content, headers={**HEADERS,
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"})


@router.get("/guide.md", response_class=PlainTextResponse)
async def guide():
    try:
        content = onboarding.guide()
    except (ValueError, FileNotFoundError):
        return unavailable()
    return PlainTextResponse(content, media_type="text/markdown", headers=HEADERS)


@router.get("/manifest.json")
async def manifest():
    try:
        content = {"format": "xianyu-ai-connection/v1", **onboarding.connection()}
    except (ValueError, FileNotFoundError):
        return unavailable()
    return JSONResponse(content, headers=HEADERS)


@router.get("/skills/xianyu-operations.zip")
async def download_skill(request: Request):
    try:
        data = onboarding.skill_bundle(onboarding.public_base())
    except (ValueError, FileNotFoundError):
        return unavailable()
    digest = hashlib.sha256(data).hexdigest()
    headers = {**HEADERS, "ETag": f'"{digest}"', "X-Skill-SHA256": digest,
               "Content-Disposition": 'attachment; filename="xianyu-operations.zip"'}
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    return Response(data, media_type="application/zip", headers=headers)


@router.get("/skills/xianyu-operations/{relative:path}")
async def skill_document(relative: str):
    if relative not in onboarding.SKILL_FILES:
        return PlainTextResponse("技能文件不存在", status_code=404, headers=HEADERS)
    try:
        data = onboarding.skill_file(relative, onboarding.public_base())
    except (ValueError, FileNotFoundError):
        return unavailable()
    return Response(data, media_type="text/plain; charset=utf-8", headers=HEADERS)
