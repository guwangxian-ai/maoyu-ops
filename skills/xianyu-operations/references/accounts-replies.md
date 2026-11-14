# 账号、聊天与回复配置

## 账号检查和设置

1. 从 `account_list_cookie_options` 和 `account_list_cookie_details_paginated` 取得当前用户账号。保存平台 `id`、本站 `pk`、备注、`enabled/online` 与本次相关开关；不把一个账号设置复制给所有账号，除非当前授权覆盖。分页要查完。
2. 账号启停用 `account_update_account_status` / `account_update_accounts_status_batch`，需要 `xianyu:account-status:write`；设置用相应 `account_update_account_*` 工具，需要 `xianyu:account-settings:write`。启用会恢复既有处理规则，不等于只开启当前商品。只改指定字段，回读确认。
3. 可配置项包括备注、人工接管暂停时间、消息有效期、回复延迟、自动确认发货、补发货、评价、擦亮、先确认/先发卡券、只发卡券、求小红花、已下单用户 AI 回复过滤、发货禁用及条件规则。各参数以 schema 为准；不要把相似开关当作同一设置，完整读取原状态后再修改。
4. `account_get_available_delivery_block_rules` 提供支持的条件，`account_get_delivery_block_rules` / `account_update_delivery_block_rules` 读写账号规则。效果还依赖 Websocket/Scheduler 和真实账号状态；请求成功不证明正在处理消息。
5. 擦亮按[发布后擦亮核验](listing.md#发布后擦亮核验)；发货按[订单流程](orders.md)。登录、Cookie 导入和风控验证见[后台边界](project-admin.md)，不要在 MCP、聊天或文档传原始凭证。

## 回复分层与一致性

先读取当前账号的 `ai_get_ai_reply_settings`、`default_replies_get_default_reply`、`keywords_get_keywords_with_item_id`，以及目标商品的 `items_get_item_ai_prompt` / `items_get_item_default_reply`。只调整当前任务相关层次，并检查旧关键词、默认回复和商品提示词是否存在冲突，不能只写 AI 提示词就声称所有自动回复已改变。

- 账号 AI 设置：`ai_update_ai_reply_settings`；批量用 `ai_bulk_update_ai_settings` 并逐账号回读。明确的 `0`（零优惠、零议价轮数等）必须保持，不替换为默认值。
- 商品 AI 提示词：`items_save_item_ai_prompt` / `items_batch_save_item_ai_prompt`；商品默认回复：`items_save_item_default_reply` / `items_batch_save_item_default_reply`。先核对 `cookie_id/item_id`，重发的新 ID 要重绑。批量输入是全量替换还是逐项修改，以真实 schema/业务说明为准，不能省略原配置造成清空。
- 账号默认回复与关键词：对应 `default_replies_*` / `keywords_*`；图片先暂存，再通过专用上传工具生成业务图片地址。导入前核对格式和覆盖范围，导出仅保存私有文件。删除工具需要独立 delete scope。
- 消息过滤：先读 `message_filters_get_message_filters`，核对规则内容、匹配范围和启用状态后新增/更新/切换，不把“没有回复”一律归因于 AI 掉线。
- 确认收货消息：`confirm_receipt_messages_*`；快捷语：`chat_quick_phrase_*`。配置消息不等于已经发送给买家。

虚拟商品沿用已批准的简短话术、付款后交付与售后约定，见[商品文案](listing.md)。售前不发分享链接/提取码；不承诺资料没有的版本、功能、模型或支持。测试先用可审查的示例问题核对配置；`ai_test_ai_reply_settings` 会调用模型，仅在测试与费用范围已批准时使用，不能把模型测试当成实际买家回复。缺少真实消息事件时明确报告“配置已回读，真实回复未验证”。

MCP 的 AI 配置 schema 刻意排除 `api_key/base_url/provider_type`。服务商密钥只能通过已授权的项目私有配置管理入口保存；复用既有密钥时不读取或抄出原文。AI 上架和 AI 回复是不同配置，不互相覆盖。

## 在线聊天

`chat_list_accounts` / `chat_list_connected_accounts` 查询聊天连接，`chat_get_conversations` → `chat_get_messages` 获取目标会话。不要把聊天管理器连接与自动发货 Websocket 在线状态混为一谈。连接/断开工具需 `messages:connection:write`，不因查询失败擅自重启账号。

发送文字用 `chat_send_message`、图片用 `chat_image_send_image`、撤回用 `chat_recall_message`，需 `messages:send:write` 和明确的发送授权。复核账号、会话、接收方、具体内容与附件；保存回执和平台消息标识，未知结果先查历史，不重发。读消息或“配置自动回复”不会自动授权主动联系历史买家。

`chat_query_official_blacklist` / `chat_change_official_blacklist` 操作平台官方黑名单；与本项目“黑名单管理”的本地规则不同。`chat_query_avatars` / `chat_get_account_profile` 查询资料，`chat_customer_order_get_customer_orders` 可关联当前客户订单，结果只保存在私有记录。

## 数据分析

`account_get_account_stats` / `account_get_order_amount_trend` 与 `data_analysis_get_seller_summary` / `data_analysis_get_browse_summary` 用于账号/店铺数据。记录账号、统计时间范围、数据来源和币种/单位；接口未提供的指标保持未知。本人订单额、平台浏览、竞品想要数和网盘拉新结算是不同指标，不能互相替代。搜索选品流程见[选品与需求验证](selection.md)。
