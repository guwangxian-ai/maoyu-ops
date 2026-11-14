# 订单卡券发货

## 订单、售后和评价能力

平台订单获取用 `orders_fetch_xianyu_orders`，本地列表和详情用 `orders_list_orders/get_order_detail`。核对账号、订单号、商品和付款/发货/退款状态及获取时间；本地旧记录不能替代当前平台状态。分页、部分失败和范围限制要明确记录。

`orders_manual_delivery` 发送卡券与 `orders_no_logistics_delivery` 平台无物流确认是不同操作。先查既有发送/确认状态，再执行当前授权覆盖的动作；订单超时或结果不明先查原回执和最新订单，不能重复发卡。`orders_cancel_order` 取消订单需独立 `delivery:cancel:write` 和订单范围，不等于退款成功或删除历史。

`auto_rate_get_auto_rate_config/update_auto_rate_config` 管理评价规则，`auto_rate_batch_rate_orders` 会对指定订单实际评价，需 `delivery:ratings:write`。`refund_cancel_get_refund_cancel_config/update_refund_cancel_config` 管理退款注销配置，`agree_deliver_get_agree_deliver_config/update_agree_deliver_config` 管理同意发货规则；先读原值再改并回读。配置这些规则不证明某笔退款、注销或同意动作已执行，也不提供任意退款接口。“不满意包退”文案不能冒充实际退款凭据。

账号自动确认、补发货、先发卡券/先确认等设置见[账号与回复](accounts-replies.md)，卡券内容与绑定见[网盘与卡券](delivery-assets.md)。每个环节分别保存结果，不把某个开关状态当作端到端发货成功。

本项目虚拟商品默认售后与“付款后秒发”措辞见[虚拟商品售后与发货承诺](listing.md#虚拟商品售后与发货承诺)。此承诺不改变付款核验、去重与回执要求，也不能仅因买家提出退款就宣称已完成退款。

## 发货前只读检查

按已批准的账号、商品和订单范围查询 `orders_list_orders` / `orders_get_order_detail`。查看 `order_no/order_id` 的真实字段，使用详情中的 `cookie_id`、`item_id`、规格、数量、金额、买家及状态。

查询商品当前情况时先按 [账号商品同步与查询](catalog-sync.md) 取得平台证据；订单商品可能已售出/下架，在售同步未返回它不能单独用于否定订单或判断商品不存在。商品同步和订单刷新是独立步骤，前者不能证明付款或可发货状态。

- 只处理本人指定账号/商品，订单号、买家和卡券必须相互对应。
- 待付款、关闭/退款、已发货/完成、`card_only_delivered=true` 或发送结果未知的订单不能直接发卡。待发货常见状态 `pending_ship/pending/paid/待发货`，仍需可靠的当前付款与可发货证据。
- `orders_get_order_detail` 的 `query.refresh=true` 会尝试刷新，但当前接口在刷新失败时仍可能返回本地旧数据，不能仅凭 success 或 refresh 参数声称已查到实时付款状态。结合近期平台订单拉取/页面记录；无法确认时停在待核对，不发送。
- 读取商品关联卡券，检查启用状态、规格唯一匹配、库存/数量。`assets_get` 确认资料版本、必要平台及有效期，正式交付前检查远端分享可用性。存在多个匹配卡券时先解决，不随机选。
- 发货依赖独立 WebSocket 服务中的账号连接。`chat_list_connected_accounts` 是另一聊天管理器，不能据此证明发货服务已连接。接口返回“账号未连接”时先恢复对应服务/账号，不改用聊天发链接绕过。
- 检查自动发货设置和既有订单发送记录，避免人工流程与自动任务重复处理。读不到可靠状态时先报告具体缺口。

## 执行与核对

`orders_manual_delivery` 是真实发送工具；参数为 `body.order_no` 和顶层 `confirm:true`、稳定 `operation_id`（脚本自动注入），不接收 AI 指定的 `card_id` 或任意消息文本。实际卡券由后端按商品/规格匹配，并使用既有发货处理。

1. 将订单、商品、资料版本/卡券、数量、授权引用保存到运行记录。没有覆盖该订单/商品范围的发货授权时，先给出具体预览再请求。
2. 使用固定编号 `account:order:deliver` 调用，保留原响应。超时后先查原订单与发送记录；禁止换编号直接重复发送。
3. 读取返回中的 `is_card_only`、`skipped_due_to_dock_card`、所有 `quantity_degraded_*`、`quantity_requested/quantity_sent`。有退化、跳过或部分发送时明确报告，不把 `success=true` 当成完整数量成功。
4. 重新查询订单 `delivery_send_status/delivery_send_fail_reason/delivery_content/card_only_delivered/status`，结合原响应核对实际发送与平台发货两项结果。若状态仍不明确，保留待核对记录。

`orders_no_logistics_delivery` 只处理平台无物流发货确认，不等于卡券已送达。不要用 `chat_send_message` 手工发相同链接替代发货流程，这会绕开卡券选取、数量和既有订单锁。

数量大于一时，固定文本卡券可能只发送一份共同链接；这不能被描述为提供多个独立兑换码/许可证。按商品承诺核对；需要独立码时使用实际具备库存的卡券类型。

## 凭证发货后补发纯文字

固定文字卡券可同时开启 `use_no_logistics_form=true` 与 `send_text_after_form=true`：先把本单文字提交为无需邮寄凭证，平台明确成功后，再向订单买家的原会话发送一份纯文字。账号须开启自动确认发货，不能使用只发卡券或同意后发货模式。卡券开关使用 `cards_update_card`（`delivery:cards:write`）；普通聊天发卡保持原逻辑。开启只影响后续发货，不追发历史订单。

- 查询 `orders_get_delivery_text`（`delivery:read`）或订单详情的 `delivery_text`；订单列表也显示独立副本状态，不能混同原发货状态。查询结果包含本单完整内容、时间和逐次发送记录，保存到私有回执目录。
- `pending` 尚未发送，账号恢复连接后队列自动处理；`sending` 正在等待平台响应；`success` 表示平台明确确认发送，不代表买家已读。`failed` 为明确拒绝/未发送；`unknown` 为超时、异常或发送中断，不能当失败重发。`shipping_unknown` 表示凭证结果未确认，不发送文字；`cancelled` 为开关、退款或订单身份变动后停止。
- `orders_retry_delivery_text`（`delivery:orders:write`）只重新排队明确失败的本单副本，累计最多 3 次；先核对失败原因并处理平台限制，使用稳定 `operation_id`。不调用 `orders_manual_delivery` 或任意聊天发送接口绕过。本功能不需要泛消息发送权限，不扩展密钥权限或期限。
- 重试使用发货时保存的内容和原买家/会话，不重新匹配卡券、不消耗新卡、不再次确认发货。数据库按账号+订单唯一去重，并保存每次发送的消息编号；并发与重启不能清除去重记录。
- 只有明确的发送成功回执才写 `success`。未知结果先检查原消息/平台凭证，不能换操作编号强发；现有接口不会强制重置未知状态。发送失败不回退已经成功的发货凭证或订单。
- 资料链接和提取码仅在已付款订单内交付，不写入公开商品页或售前 AI 回复；更新卡券后核验开关与账号发货配置。没有真实订单验收时，明确只完成隔离流程测试和线上配置核验。

## 当前验收边界

本技能提供真实工具的完整操作流程，不含虚构测试订单或“模拟买家已收到”。没有获准的真实订单时完成只读检查、参数预览和双网盘卡券链路演练，并明确真实发货尚未验证。全自动实际发货前仍需完成一单受控验收，核对平台状态、买家收到的内容及重复执行行为。
