# 项目能力索引

核对版本：2026.11.14.1。依据项目导航、`backend-web/app/api/routes`、MCP 白名单与运行实例。先定位任务所在行，再加载对应流程；不必一次读取所有文档。本文描述项目能力，**不代表当前密钥拥有全部权限，也不代表已经完成业务验证**。工具名以下划线形式列出；客户端可能再加 `mcp__xianyu__` 前缀。

执行时用 `mcp_capabilities`、`tools/list` 或 Skill `schema --tool ...` 核对实际参数和 scope。四域 read、细分 write、delete 独立；写工具还须已有业务授权和稳定操作编号。当前工具目录会按权限过滤，数量不能作为完整覆盖的证明。没有 MCP 的功能见最后一节，不能猜工具名、直接改库或自动扩大密钥权限。

## 已开放 MCP 的功能

下表“源码模块”对应路由文件，供维护者检查覆盖；“工具入口”是代表性入口，完整参数与同组工具以实时目录为准。

| 能力 | 源码模块 | 工具入口与结果 | 工作流 |
| --- | --- | --- | --- |
| 账号、启停、备注、回复/发货/擦亮等开关、统计 | `cookies` | `account_list_cookie_details_paginated`、`account_update_account_auto_polish`、`account_get_account_stats`；逐项回读，不暴露 Cookie | [账号与回复](accounts-replies.md) |
| 本人商品同步、在售检查、筛选、详情、本地/平台编辑、库存、多规格、多数量、下架/删除、商品回复 | `items` | `items_fetch_all_items_from_account`、`items_list_items_paginated`、`items_update_seller_item`；区分本地与平台结果 | [同步](catalog-sync.md)、[商品操作](listing.md) |
| 卡券关联的本站轻量商品选择 | `selectable_items` | `items_get_selectable_items_paginated`；只读分页、仅名称/ID搜索，不确认平台在售 | [同步](catalog-sync.md) |
| 平台竞品搜索、鱼小铺搜索 | `search`、`goofish_compass` | `search_search_items`、`goofish_compass_search_goofish`；保留真实样本与人数来源 | [选品](selection.md) |
| 竞品采样、历史、候选证据与失败恢复状态 | `market_research` | `market_collect_market_sample`、`market_list_market_samples`、`market_get_market_history`、`market_get_market_candidates`、`market_list_market_monitor_states`；均为本人研究查询，不创建持续任务 | [竞品研究](market-research.md) |
| 店铺销售/浏览数据、订单金额趋势 | `data_analysis` | `data_analysis_get_seller_summary`、`data_analysis_get_browse_summary`；趋势另有 `account_get_order_amount_trend` | [账号与回复](accounts-replies.md) |
| 素材、图/视频上传、分类推荐、单品/批量发布、日志、自动重发规则与事件 | `product_publish` | `publish_list_materials`、`publish_publish_single`、`publish_get_batch_status`、`publish_get_auto_relist_rule`；发布与重发分开授权 | [商品操作](listing.md) |
| 账号发布能力、个人/公共地址查询、个人地址维护、导入导出、地点提示 | `product_publish_capability`、`publish_addresses`、`personal_addresses` | `account_get_publish_account_capability`、`address_list_publish_addresses`、`personal_address_list_personal_addresses` | [商品操作](listing.md) |
| AI 上架配置查询、任务创建/取消/进度 | `ai_listing` | `ai_listing_list_configs`、`ai_listing_create_task`、`ai_listing_get_task`；模型配置维护未开放 | [商品操作](listing.md) |
| 卡券查询、创建、编辑、图文/多规格、商品关联与批量绑定/清理 | `cards` | `cards_get_cards_by_item`、`cards_create_card`、`cards_update_item_cards`；不等于真实订单发货 | [网盘与卡券](delivery-assets.md) |
| 订单查询/平台获取、详情、卡券发送、无物流确认、取消 | `orders`、`chat_customer_order` | `orders_fetch_xianyu_orders`、`orders_get_order_detail`、`orders_manual_delivery`、`orders_get_delivery_text`、`orders_retry_delivery_text`、`chat_customer_order_get_customer_orders` | [订单](orders.md) |
| 发货同意、退款注销配置、自动评价与批量评价 | `agree_deliver`、`refund_cancel`、`auto_rate` | `agree_deliver_get_agree_deliver_config`、`refund_cancel_get_refund_cancel_config`、`auto_rate_batch_rate_orders`；规则与真实动作分开核验 | [订单](orders.md) |
| 账号默认回复、关键词/图片关键词、导入导出、商品专属回复 | `default_replies`、`keywords` | `default_replies_get_default_reply`、`keywords_get_keywords_with_item_id`；商品专属配置在 `items_*` | [账号与回复](accounts-replies.md) |
| AI 回复开关、模型参数/提示词、批量设置、测试 | `ai` | `ai_get_ai_reply_settings`、`ai_update_ai_reply_settings`、`ai_test_ai_reply_settings`；不通过 MCP 设置服务商密钥 | [账号与回复](accounts-replies.md) |
| 消息过滤、确认收货消息/图片、快捷语 | `message_filters`、`confirm_receipt_messages`、`chat_quick_phrase` | `message_filters_get_message_filters`、`confirm_receipt_messages_get_confirm_receipt_message`、`chat_quick_phrase_list_quick_phrases` | [账号与回复](accounts-replies.md) |
| 聊天连接、会话/历史消息、发送文字/图片、撤回、头像/资料、官方黑名单 | `chat_new`、`chat_new_image` | `chat_list_connected_accounts`、`chat_get_messages`、`chat_send_message`、`chat_image_send_image`、`chat_query_official_blacklist` | [账号与回复](accounts-replies.md) |
| 夸克授权会话、文件/搜索/浏览、上传下载/续传、复制整理/移动/转存、分享与更新、重命名计划、问答/摘要、任务 | `quark_drive` | `quark_capabilities`、`quark_files`、`quark_upload`、`quark_create_share`、`quark_job`；不支持的能力不可据工具名称猜测 | [网盘与卡券](delivery-assets.md) |
| 百度授权会话、文件/元信息、上传下载、建目录/复制/移动/重命名、分享查询/撤销 | `baidu_drive` | `baidu_capabilities`、`baidu_files`、`baidu_upload`、`baidu_create_share`；删除/撤销另需权限 | [网盘与卡券](delivery-assets.md) |
| 统一资料上传/文件或链接/分享导入、版本、分析、同步、多盘分享、失败重试、生成卡券 | `cloud_assets` | `assets_get`、`assets_sync`、`assets_shares`、`assets_analyze`、`assets_card`；按版本和阶段查询验收 | [网盘与卡券](delivery-assets.md) |

## 接入和执行基础能力

`mcp_capabilities` 发现能力；`mcp_upload_file` 或带认证的暂存接口上传文件，`mcp_read_file` 分块读结果；`mcp_operation_get` / `mcp_operations_list` 查询持久回执。[执行与恢复](execution.md) 包含 `doctor / schema / call / resume / operations / receipt`、大文件与异步恢复、权限诊断。它们不提供任意 HTTP、Shell 或管理员透传。

## 项目具备、但未完整开放 MCP 的功能

详见 [后台能力与接入边界](project-admin.md)：登录/扫码/风控验证、通知与本地黑名单、分销、监控配置与采集/私信/下单任务、管理日志与调度、备份、用户/系统/支付/广告等。索引收录这些能力是为了正确分流；只有用户授权且现有管理入口合适时才在那里操作。用户明确要求 MCP 时，报告缺口，不能悄悄改用其他方式执行。

新增项目能力时同时更新本索引、对应流程及公开包白名单；新增 HTTP 路由不会自动成为 MCP 工具。只有明确需要扩展 MCP 时才审查用户归属、最小 scope、凭证/文件边界、回执与运行验证。不会为了“文档覆盖全部能力”把管理员接口一并开放。
