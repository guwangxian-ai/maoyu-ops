# 后台能力与接入边界

这些能力已存在于项目，当前 MCP 未开放或只开放其中一部分。页面路径相对于实例根路径（例如 `/maoyu-ops/`），不是让已有 MCP 用户重新登录 UI 的前置要求。只在任务需要且用户授权相应管理操作时选用现有入口；明确限定 MCP 的任务先说明缺口，不直接改业务数据库或借外部接口绕过权限。

## 未开放 MCP 的功能组

| 功能与现有入口 | 源码路由模块 | 处理与验收边界 |
| --- | --- | --- |
| 账号登录、导入授权、扫码/密码/共享扫码、验证码/人脸、Cookie 刷新 | `qr_login`、`qrcode`、`password_login`、`shared_scan`、`captcha`、`geetest`、`face_verification`、`cookie_refresh` | 账号页 `/accounts`；人工完成扫码/验证码，分别核对登录成功和业务在线状态。不能代过验证或输出原始 Cookie |
| 本地黑名单、通知渠道、消息通知 | `blacklist`、`notifications` | `/blacklist`、`/notification-channels`、`/message-notifications`；平台官方黑名单另有 MCP。测试通知会真实外发，不能作为无副作用读检查 |
| 消息/自动回复/风控/登录日志，聊天实时通道 | `message`、`auto_reply_logs`、`risk_control_logs`、`account_login_logs`、`user_login_logs`、`chat_new_ws` | `/message-logs`、`/risk-logs` 和管理日志；WebSocket 传输不是一个任意可调用的 MCP 工具。只读取必要脱敏证据 |
| 分销货源、对接卡券、分销商/代理订单 | `distribution`、`card_dock` | `/distribution/*`；MCP 卡券绑定只支持本人卡券和本人商品，不能冒用分销对接卡券。涉及资金/采购另核授权 |
| 监控分类、商品上新监控、采集商品、采集/下单备用账号、采集任务 | `listing_monitor`、`listing_monitor_category`、`goofish_crawler`、`collect_fallback_account`、`order_fallback_account` | `/product-monitor/*`；一次搜索不启用持续监控、自动私信或自动下单，分别检查任务配置、状态和日志 |
| 用户登录/角色、激活、个人设置、账户余额与支付 | `auth`、`users`、`user_settings`、`activation`、`payment` | 登录、`/personal-settings`、管理用户入口；普通业务令牌不等于管理员会话。不得通过能力补齐改变费用或权限 |
| MCP 令牌、公开接入和系统配置/运行/版本/代理/健康/通用上传 | `mcp_access`、`ai_connect`、`system_settings`、`system_control`、`version`、`proxy`、`health`、`upload` | `/ai-access`、公开 `/ai`、`/settings`；公开包不含密钥。通用上传不能绕过 MCP 暂存、归属及媒体路径检查 |
| 管理员统计/用户/定时任务/擦亮和其他批次日志 | `admin`、`token_renewal_logs`、`cookies_refresh_logs`、`api_cookie_renew_logs` | `/admin/*`；仅管理员可见，当前 MCP 不提供调度启停或触发、管理员日志透传。业务开关开启后还须核对调度运行 |
| 数据库备份与邮件日志 | `db_backup_logs`、`email_logs` | `/admin/db-backup-logs`、`/admin/email-logs`；备份创建/下载/删除与只读日志不同，部署是否备份遵循当前项目授权，不能默认生成 |
| 公告、弹窗、广告、反馈 | `announcements`、`popup_announcements`、`advertisements`、`feedback` | 管理公告/广告、`/ad-apply`、`/feedback`；对外发布或提交须在已授权范围内 |
| 外部集成 API | `external_accounts`、`external_category`、`external_cookie`、`external_message_logs`、`external_orders`、`external_publish` | 面向独立外部接入，不是 MCP 的备用万能通道；不通过它取密钥、绕过归属或回执 |
| 同意自提 | `agree_pickup` | 平台订单操作；不与 `agree_deliver_*` 发货同意配置混用，未提供对应 MCP 工具 |

## 已有 MCP 模块中的未开放部分

- 账号：新增/导入/导出、Cookie/密码更新、登录续期、删除、批量关闭通知/清令牌缓存等不在 MCP 白名单。`account_*` 仅提供[能力索引](capabilities.md)列出的安全查询和规则操作。
- AI 回复：服务商凭证和模型列表获取未开放。AI 上架：可读取配置、创建/查询/取消任务，不能经 MCP 新建/修改/删除模型配置或测试服务商连接。
- 公共发布地址：只提供查询与地点提示；维护公共地址及状态仍在页面。个人地址有独立 `personal_address_*` 工具。
- 发布日志清空、订单记录删除、同意发货的自提 URL 建议未开放；取消订单不能当作删除记录或退款。
- 夸克旧凭证导入回调、百度底层配置/授权完成回调未开放。改用正式授权会话，不把原始凭证塞进其他字段。

## 后台工作原理与部署验收

Backend-Web 提供页面/API/MCP，Websocket 处理自动回复及订单事件，Scheduler 执行定时擦亮、补发/评价、商品/订单获取、续期、监控等，MySQL/Redis 保存业务和运行状态。配置读写成功、服务健康、平台业务成功是三层证据；不能仅凭健康接口证明发布/擦亮/发货成功。

仅修改 Skill 不会开启任何任务。账号掉线恢复也不等于普通发布自动补发；专门自动重发规则见[商品操作](listing.md)。后台任务的间隔和启用值以当前实例配置为准，不能根据源码默认值宣称正在运行。

只有当前任务授权部署时才进行部署；读取项目 `AGENTS.md` 和部署记录、确认本轮备份选择，保留数据库、凭证与运营资料。已明确“不备份”就直接沿用，不重复询问也不创建归档。验收包括必要的隔离测试、服务健康、公开包版本/哈希、MCP 初始化说明、本人权限与业务回读，并检查共享服务器其他项目。SSH 只用于获准的服务器管理，经营写操作继续走 MCP 回执。

竞品按次采样、历史/候选和已有监控失败状态已开放为 `market_research` 的五个只读 MCP 工具，见 [竞品研究](market-research.md)。本页的监控缺口指创建/配置持续任务及私信/下单等操作，不能据查询能力自动启用它们。

## 通知分类与注册校验（2026.11.14.1）

- 通知渠道和账号订阅可分别选择 chat（聊天）、delivery（发货相关异常）、account（账号异常/令牌刷新），两级都选中才推送该事件。只需账号提醒时选择 account；通知发送仍需渠道及账号绑定启用。历史空值和空选择兼容为全开；完全停止推送应关闭对应绑定或渠道。旧客户端只切换启用开关不重置已有类型。
- 配置入口仍为 notification-channels/message-notifications，当前没有通知配置 MCP 工具；新增轻量商品查询工具不增加通知管理权限。测试通知会真实外发，只在用户明确授权时执行。
- 自助注册、注册邮件发送和校验在服务端读取 registration_enabled；缺失默认关闭。注册开关变化不授权修改现有用户，读取失败不能宣称管理员关闭。注册页可重试读取，现有登录不依赖注册开放。
- 单品发布的能力检测、载荷、发布器和所在地查询由 common 模块共享；原网页后端导入路径保留兼容，不能为修复 Scheduler 导入而扩大 app 包搜索路径。
