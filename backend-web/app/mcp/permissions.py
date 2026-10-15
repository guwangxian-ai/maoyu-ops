"""Business permissions shared by discovery, enforcement and credential UI.

Legacy domain write grants retain exactly their old domain's write access.
New credentials select individual capabilities; no implicit read or delete grant.
"""
from __future__ import annotations

READ_SCOPES = {
    "xianyu:read": ("闲鱼查询", "账号、商品、搜索及发布状态；同步可更新系统副本"),
    "drive:read": ("网盘查询", "授权状态、文件、分享、下载和任务查询"),
    "delivery:read": ("订单与卡券查询", "订单、卡券和发货配置查询"),
    "messages:read": ("消息查询", "会话、消息和回复配置查询"),
}
WRITE_SCOPES = {
    "xianyu:account-status:write": ("账号启停", "启用或停用账号及其已有消息和发货任务"),
    "xianyu:account-settings:write": ("账号规则设置", "修改账号回复、发货、定时任务等已有规则"),
    "xianyu:items:write": ("商品编辑", "修改商品资料、价格、库存、规格和商品回复设置"),
    "xianyu:offline:write": ("商品下架", "停止指定平台商品销售，保留系统记录"),
    "xianyu:publish:write": ("商品发布", "发布新商品、提交或取消 AI 上架任务"),
    "xianyu:materials:write": ("素材与地址维护", "创建或修改发布素材、上传媒体、维护发布地址"),
    "xianyu:automation:write": ("重新上架规则", "配置或处理自动重新上架任务"),
    "drive:share:write": ("创建网盘分享", "分享已有文件、目录或已同步资料"),
    "drive:files:write": ("网盘文件操作", "上传、移动、复制、改名、转存和网盘任务操作"),
    "drive:auth:write": ("发起网盘授权", "发起或取消授权会话；验证码仍由用户完成"),
    "drive:assets:write": ("资料同步与版本", "导入资料、管理版本及跨盘同步；重试分享另需分享权限"),
    "delivery:cards:write": ("卡券配置与绑定", "创建或修改卡券及商品关联，不授予真实订单发货"),
    "delivery:orders:write": ("真实订单发货", "发送订单卡券或确认平台无物流发货"),
    "delivery:cancel:write": ("取消订单", "取消指定订单"),
    "delivery:settings:write": ("发货与评价规则", "配置发货、退款注销及自动评价规则"),
    "delivery:ratings:write": ("订单评价", "对指定订单提交评价"),
    "messages:send:write": ("发送与撤回消息", "向买家发送文字、图片，或撤回已发送消息"),
    "messages:connection:write": ("聊天连接", "连接或断开在线聊天管理器中的账号"),
    "messages:settings:write": ("回复与会话设置", "维护关键词、默认或 AI 回复、过滤规则、快捷语及黑名单"),
}
DELETE_SCOPES = {
    "xianyu:delete": ("删除商品或素材", "删除平台或系统商品、发布素材、地址及商品回复配置"),
    "drive:delete": ("删除网盘内容或撤销授权", "删除支持的文件、取消分享或撤销网盘授权"),
    "delivery:delete": ("删除卡券或关联", "删除卡券及其商品关联"),
    "messages:delete": ("删除回复配置", "删除回复、关键词、过滤规则或快捷语"),
}
SCOPE_INFO = {**READ_SCOPES, **WRITE_SCOPES, **DELETE_SCOPES}
LEGACY_WRITE_SCOPES = {f"{domain}:write" for domain in ("xianyu", "drive", "delivery", "messages")}
SCOPES = set(SCOPE_INFO) | LEGACY_WRITE_SCOPES


def permits(granted, required: str) -> bool:
    return required in granted or (required in WRITE_SCOPES and required.split(":")[0] + ":write" in granted)


def expand_legacy_scopes(granted) -> list[str]:
    """Equivalent migration, never grants a new domain, read or delete ability."""
    result = set(granted) - LEGACY_WRITE_SCOPES
    result.update(scope for scope in WRITE_SCOPES if scope.split(":")[0] + ":write" in granted)
    return sorted(result)


def scope_options() -> list[dict]:
    return [{"id": scope, "label": label, "description": detail}
            for scope, (label, detail) in SCOPE_INFO.items()]


def can_upload(granted) -> bool:
    return any(permits(granted, scope) for scope in (
        "drive:files:write", "drive:assets:write", "xianyu:materials:write",
        "xianyu:publish:write", "xianyu:items:write", "delivery:cards:write",
        "messages:send:write", "messages:settings:write"))


def write_scope(module: str, fn: str, domain: str, operation: str | None) -> str:
    if module == "cookies":
        return "xianyu:account-status:write" if fn in {"update_account_status", "update_accounts_status_batch"} else "xianyu:account-settings:write"
    if domain == "xianyu":
        if module == "items":
            return "xianyu:offline:write" if fn == "batch_offline_items" else "xianyu:items:write"
        if module == "ai_listing" or fn in {"publish_single", "publish_batch"}:
            return "xianyu:publish:write"
        if fn in {"save_auto_relist_rule", "reconcile_auto_relist_event", "mark_auto_relist_event_failed"}:
            return "xianyu:automation:write"
        return "xianyu:materials:write"
    if module == "cloud_assets" and fn == "card":
        return "delivery:cards:write"
    if domain == "drive":
        if fn in {"quark_create_share", "create_share"} or module == "cloud_assets" and fn == "shares":
            return "drive:share:write"
        if "auth" in fn:
            return "drive:auth:write"
        return "drive:assets:write" if module == "cloud_assets" else "drive:files:write"
    if domain == "delivery":
        if module == "cards":
            return "delivery:cards:write"
        if module == "orders":
            return "delivery:cancel:write" if fn == "cancel_order" else "delivery:orders:write"
        return "delivery:ratings:write" if fn == "batch_rate_orders" else "delivery:settings:write"
    if fn in {"send_message", "recall_message", "send_image"}:
        return "messages:send:write"
    if fn in {"connect_account", "disconnect_account"}:
        return "messages:connection:write"
    return "messages:settings:write"
