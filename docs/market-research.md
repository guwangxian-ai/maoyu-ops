# 竞品研究与选品证据

2026-11-04，版本 2026.11.04.1。服务于虚拟商品经营者及通过 MCP 操作的 Agent：保留多次市场样本，解释哪些需求信号值得继续验证。页面位于 `/maoyu-ops/product-monitor/research`，沿用现有后台布局与账号。

## 本轮能力

1. 现有 MTOP 搜索/详情客户端进行按次采样，或接收已有监控任务的采集结果。
2. MySQL 新增 `xy_market_sample_runs`、`xy_market_observations`、`xy_market_monitor_states`。原商品、卡券、AI 回复、订单和监控配置不迁移；只加表，不改旧表结构。
3. 查询批次、单品历史、同范围候选和价格摘要；只有可比端点才计算想要数、浏览量或价格净变化。未知不置零、不同来源不混算、近似人数不算精确增长，未搜到不等于下架。
4. 关键词预筛返回命中/排除理由，已有 Agent 根据证据及真实交付资源判断候选；不增加后台付费模型配置、调用或自动跟卖。
5. 既有监控连续三次失败/部分采集后持久暂停，15 分钟起，后续失败逐级延长至最多 4 小时。按原启用调度试采，成功后恢复；停用任务/账号保持停用。异常由研究页面和 MCP 状态查询呈现，不开通外部通知渠道。

## API / MCP

| HTTP（前缀 `/api/v1/market-research`） | MCP 工具 |
| --- | --- |
| `POST /samples` | `market_collect_market_sample` |
| `GET /samples` | `market_list_market_samples` |
| `GET /history/{item_id}` | `market_get_market_history` |
| `GET /candidates` | `market_get_market_candidates` |
| `GET /monitor-states` | `market_list_market_monitor_states` |

全部工具使用既有 `xianyu:read`：只查询平台并保存本人研究副本，不扩展既有密钥权限或有效期。普通后台和管理员调用也按当前用户 ID 过滤研究数据；不借管理员权限查询其他用户的样本。原监控配置/私信/下单接口未加入 MCP 白名单。

`sample_key` 是采样的稳定编号，唯一约束为用户+编号；首轮先落批次，重试返回原状态，相同编号不同参数拒绝。中断批次不可伪装为完成。来源、条件、范围通过 `scope_key` 分开，同一批次按商品 ID 去重。查询失败不删除已有观测；`latest_attempt` 显示后续失败，避免误用旧成功。

## 资源与验收

按次采样最多 3 页×30 件、10 个顺序详情，90 秒截止；当前单 Backend-Web 实例同时只接纳一轮，忙时拒绝排队。候选最多查询最新批次 300 件，通过 SQL 窗口选端点和分组统计，不逐件查询全部历史；返回最多 100 件。若以后部署多个 Backend-Web 副本，需将本进程采样准入锁升级为跨实例锁。

历史仅从上线后开始，最多查询 90 天；已有 Scheduler 每小时最多清理 2000 条超过保留期的新研究历史。不新增独立守护进程、数据库或依赖。既有监控超过 300 件时仅历史副本标部分，不降低原采集范围，也不把历史限额当采集故障。

验收覆盖：真实独立数据库及 API/MCP、跨用户隔离、失败/部分/未知/来源切换、稳定编号重放、暂停恢复、分页/查询数、页面桌面/移动端及加载/空/错误状态。平台小样本采集与多日趋势分开验证；不能用短间隔验证样本冒充已发现市场增长。部署/实测结果见当轮部署记录，未验证项目需明确保留。

## 参考来源

参考 [Usagi-org/ai-goofish-monitor](https://github.com/Usagi-org/ai-goofish-monitor/tree/77f8d75869acd65f9dba72731b95c7c49ea67bdf)（MIT）中的快照先于分析、关键词筛选和失败保护思路。没有复制其 SQLite、Vue、浏览器调度或原销售品类提示词；实现沿用本项目 SQLAlchemy/MySQL、React 和 MTOP 客户端。原项目许可保持不变，无新增运行依赖。

运营流程集中在 [Skill 竞品研究](../skills/xianyu-operations/references/market-research.md)，避免在多处复制 Agent 操作步骤。
