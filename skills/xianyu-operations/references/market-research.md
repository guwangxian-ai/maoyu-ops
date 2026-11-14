# 竞品历史、候选证据与采集恢复

版本 2026.11.04.1。适用于多次采样、需求变化、虚拟商品选品或监控中断检查；先发现实时 schema。下面五个工具使用既有 `xianyu:read`，仅读取平台并保存本人研究副本，不创建持续任务、调用付费模型、发布、私信或下单。

## 复用已有证据，再按需采样

1. `market_list_market_samples`：按关键词查本人最近批次，检查时间、`filters`、`source`、`status`、页数和 `scope_key`。范围匹配且仍有效的证据直接复用。
2. 缺证据时用 `account_list_cookie_options` 选择本人启用账号，取闲鱼账号 `id`（非数据库 `pk`）。调用 `market_collect_market_sample`，先一页 20 件、`detail_limit=0`；确需详情人数/浏览量再限量补全。示例中的账号和编号须换成真实值：

   ```json
   {"body":{"keyword":"小店进销存表格","account_id":"实际闲鱼账号ID","sample_key":"research-20261104-unique01","pages":1,"page_size":20,"start_page":1,"detail_limit":0}}
   ```

   先保存 `sample_key`、参数和原始回执到当次私有目录。超时/断线后**沿用原编号和参数**恢复原批次，不能换号盲重采。同编号不同参数会被拒绝；超过 5 分钟仍未完成显示 `interrupted`，明确中断后再开始新的实际采样。无需 `confirm`/`operation_id`；这里是独立采样编号，不是发布/发货回执。
3. 必须查看 `data.run.status`：`success` 仅表示请求范围完成，不是全市场；`partial` 为部分样本；`failed/running/interrupted` 不能报告已完成或零需求。页数、条数以实际返回为准。登录/验证失败交还用户，不换账号绕过。
4. 当前采样仅查个人卖家，受账号、关键词、页码和排序影响。单次最多 3 页、每页 30 件、10 件详情、90 秒；每个后端实例同时仅接纳一次按次采样。详情失败保留已知列表值与真实来源，未知不填 0。

## 候选、趋势和 AI 判断

- `market_get_market_candidates`：传批次 `scope_key`，默认最近 30 天（最多 90），最多返回 100 件；`include_terms`/`exclude_terms` 是逗号分隔的任意匹配/排除词，各最多 10 个。返回该范围最新有效批次；`latest_attempt` 同时显示后来失败，不能把旧成功称为刚成功。
- `market_get_market_history`：传平台 `item_id`，建议加同一 `scope_key`，分页读指标、来源和时间。不传范围可查看多个范围，但不能混算增长。新商品 ID 建新基线，未返回不代表售罄/违规。
- `summary` 是本轮去重样本与挂牌价统计。`sample_count`、`evidence_ids`、`first_observed_at`、`observed_at` 是判断的依据，记录批次和范围。累计人数除以天数不等于近期增长。
- `changes.*.value=null`：`need_two_samples` 缺第二次不同时间观测；`missing_value` 端点未知；`source_changed` 来源变化；`rounded_counter` 近似人数；`counter_decreased` 人数回落。不把 null 改成 0，不将搜索标签与详情 `wantCnt` 直接相减，`browseCnt` 缺失也保持未知。
- `observed_growth` 仅表示可比端点的净增长；`needs_review` 不是坏商品；`filtered_out` 只是关键词结果。`analysis_status=agent_review_required` 和 `model_calls=0` 表示尚待当前 Agent 判断，不能称为后台 AI 已推荐。

Agent 结合真实样本与店主现有合法交付资源，按 [选品流程](selection.md) 给出至多三个候选并推荐一个：具体需求、证据、同质化/价格、能做出的交付差异、许可及平台准入缺口、最低成本验证办法。缺销量、素材或转售权时明说，不从“有人想要”推导可直接跟卖。候选说明与回执保存到本轮私有记录，发布等授权保持独立。

## 已有监控的失败与恢复

`market_list_market_monitor_states` 读取本人既有监控的失败次数、原因、`paused_until`、最近检查/成功时间和批次。连续三次失败/部分成功后暂停，首次 15 分钟，持续失败逐级延长至最多 4 小时；状态在数据库，重启不清零。页面“商品监控 → 竞品研究”显示异常提醒，不自动开通外部通知渠道。

用户完成扫码/验证后，到冷却时间按**原有已启用调度**试采，真实采集成功才清除失败状态；Cookie 改变不等于恢复。手动停用的任务/账号保持停用，手动执行也不绕过冷却。采集恢复不代表旧订单补发，订单仍走 [发货流程](orders.md)。创建监控、切换账号、私信/下单配置仍未开放 MCP，见 [后台边界](project-admin.md)。

## 保留与限制

历史从上线后的采样开始，不补造旧时间点，独立于本人商品、卡券、AI 回复和订单。最长查询 90 天；既有 Scheduler 按小时分批清理超过 90 天的新研究历史，每次最多 2000 条，不碰运营数据。监控单次历史最多保存 300 件，超量标为部分；原监控商品采集范围及配置不因此缩小。

没有两次可比历史时，可以报告链路可用，不能报告已发现增长或提高销量。旧实例缺新工具时，可回退 `search_search_items` 并保存本轮证据，明确未进入服务器历史；不杜撰工具或擅自启用调度。
