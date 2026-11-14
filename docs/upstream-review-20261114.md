# 上游更新评估（2026-11-14）

本轮只读取公开 GitHub 和本地源码，保存评估；没有合并业务代码、运行上游脚本、操作账号或部署服务器。

## 版本范围

- 上次整合记录：[2026-10-25 上游更新](upstream-update-20261025.md)，基线 `990f701fe03d7f62c3cf1fe571fc5bb6de57c778`。
- 当前上游 main：`1e4831391d6af6c227e39a8e622fecb009549a9c`，提交时间北京时间 **2026-11-13 18:19:37**。
- [比较范围](https://github.com/zhinianboke/xianyu-auto-reply/compare/990f701fe03d7f62c3cf1fe571fc5bb6de57c778...1e4831391d6af6c227e39a8e622fecb009549a9c)：新增 **27 个提交，涉及 275 个文件**。模块搬迁计入文件数，不等于 275 项新功能。
- GitHub Releases 查询为空，本次属于源码提交更新。
- 当前本地部署记录指向 `20261106T070000Z-delivery-text`；这仅用于识别本地记录，本轮没有连接服务器核验线上版本。

## 实际变化

| 范围 | 变化与证据 |
| --- | --- |
| 通知分类 | 最新提交将聊天、发货失败、账号异常区分为 chat/delivery/account；账号订阅与通知渠道的类型设置取交集，旧客户端省略字段时保留已有设置。适合只保留掉线和发货失败提醒。[提交](https://github.com/zhinianboke/xianyu-auto-reply/commit/1e4831391d6af6c227e39a8e622fecb009549a9c) |
| 商品定时发布 | 新增规则、触发状态与执行记录。可选择账号和素材、每日执行时段、随机分钟间隔；按同一规则和账号排除已经成功发布的素材。失败有记录，不能将此概括为所有旧失败任务上线后都会自动补发。[实现](https://github.com/zhinianboke/xianyu-auto-reply/blob/1e4831391d6af6c227e39a8e622fecb009549a9c/common/services/scheduled_publish_task.py) |
| 素材价格配置 | 普通素材新增固定/区间随机价格，多规格可分别配置区间，价格按分处理并保持库存。你本地 AI 生成发布已有价格区间，但普通素材模型尚无此次新增字段，不能将上游能力与现有 AI 价格模式混为一谈。[提交](https://github.com/zhinianboke/xianyu-auto-reply/commit/f96d7d9290fffc7f1de90d7ef1b0134d769e7d29) |
| API 关键词回复 | 匹配关键词后调用 GET/POST 接口并返回结果；支持绑定商品、有效订单资格、按订单购买数量限制成功次数、相同消息去重及同步确认发货。还增加编辑版本校验、软删除、分段恢复等保护。它不等于大模型 AI 回复质量升级，也不能保证外部 API 操作绝对只执行一次。[配置模型](https://github.com/zhinianboke/xianyu-auto-reply/blob/1e4831391d6af6c227e39a8e622fecb009549a9c/common/schemas/api_keyword.py) |
| 商品查询 | 商品管理减少重复查询；卡券关联商品使用轻量分页选择；新增排序与筛选索引。没有运行性能测试，不报告提速比例。大表首次建索引仍需评估资源。[说明](https://github.com/zhinianboke/xianyu-auto-reply/blob/1e4831391d6af6c227e39a8e622fecb009549a9c/README.md#商品查询性能与更新) |
| 发布模块共用 | 网页发布、公开发布、自动续售、定时发布共用 common 中的能力检测、载荷、发布器和快照模块，移除对 backend-web 文件路径的动态依赖。Scheduler 补齐相关依赖，需要合并构建而非仅重启。[提交](https://github.com/zhinianboke/xianyu-auto-reply/commit/25cabf444b0b01a58bf93458db59e623dfde3512) |
| 注册与邮件验证码 | 后端执行“允许用户注册”开关，注册、注册验证码发送与验证保持一致；邮箱验证码发送成功后开始有效期，发送失败不误作废其他请求的验证码。关闭注册不影响已有用户登录。[注册修复](https://github.com/zhinianboke/xianyu-auto-reply/commit/45ee5b41a61fbdb3bf0b7bf0105bf64c715d58ac)、[后续完善](https://github.com/zhinianboke/xianyu-auto-reply/commit/25cabf444b0b01a58bf93458db59e623dfde3512) |
| 个人 Token 配置 | 普通用户可设置自己的远程取 Token 地址与密钥，按账号所属用户选配置，未配置时沿用系统项；配置不完整或读取失败时明确失败，不混用其他配置。这是闲鱼连接 Token，不是 MCP 密钥或 DeepSeek 密钥。[说明](https://github.com/zhinianboke/xianyu-auto-reply/blob/1e4831391d6af6c227e39a8e622fecb009549a9c/README.md#个人-token-远程配置) |
| 在线安装 | 新增网页 SSH 测试、安装任务、增量日志与任务互斥。源码显示请求日志会记录完整请求体，可能暴露传入的 SSH 密码或私钥；不能因任务缓存不保存凭证就认定全链路不留凭证。[具体代码](https://github.com/zhinianboke/xianyu-auto-reply/blob/1e4831391d6af6c227e39a8e622fecb009549a9c/backend-web/app/api/routes/online_install.py#L62) |
| 后台运营 | 新增公开服务状态和汇总数接口、公开首页及广告位置、系统充值/广告付款的码支付支持、管理员结算记录管理及设置页整理。这些支付是系统自身业务，不是闲鱼订单支付。[README](https://github.com/zhinianboke/xianyu-auto-reply/blob/1e4831391d6af6c227e39a8e622fecb009549a9c/README.md) |

## 对 MaoYuOps 的建议

1. **优先补注册服务端校验**。本地 `backend-web/app/api/routes/auth.py` 的 `register_user` 尚未读取 `registration_enabled`，静态代码存在同类缺口。本轮未对生产接口创建账号或尝试绕过配置。
2. **其次吸收通知分类、商品查询优化与发布公共模块修复**。与你的账号掉线、发货异常定位、卡券绑定和多入口发布直接相关。必须保留现有商品状态快照、卡券和回复。
3. **定时发布按运营需求接入**。保留全部指定账号、每账号去重、付款交付、最大库存、文案合规及现有暂停状态；不能仅启用上游任务就视为这些要求已满足。
4. **API 关键词按实际接口业务采用**。当前固定网盘链接交付仍可用卡券；与已有付款后文字副本、订单去重和发送回执整合后才能上线。
5. **暂不直接引入网页在线安装与站点商业化模块**。前者有已观察到的凭证日志问题，且与当前固定主机 SSH 部署流程重叠；广告、余额支付、结算对于自营虚拟商品不是本轮优先项。

变更清单没有修改 `common/services/geetest/` 或 `backend-web/app/api/routes/geetest.py`。不能将这轮升级当成此前家中网络出口故障的修复；本轮没有重新测量该网络。

这批变更没有你二开项目的 MCP/Skills 更新。以后若合入，要同步对应工具、权限、操作回执和 Skill 流程，保留 `/maoyu-ops/` 子路径与现有授权、订单、卡券及纯文字发货记录。当前工作区存在此前未提交的二开内容，不能直接覆盖或回滚。

## 提交目录（北京时间）

| 日期时间 | 提交 | 作者提交标题 |
| --- | --- | --- |
| 2026-11-13 18:19 | [1e48313](https://github.com/zhinianboke/xianyu-auto-reply/commit/1e4831391d6af6c227e39a8e622fecb009549a9c) | feat(notification): 通知按事件类型订阅，支持渠道级开关 (#368) |
| 2026-11-13 13:40 | [25cabf4](https://github.com/zhinianboke/xianyu-auto-reply/commit/25cabf444b0b01a58bf93458db59e623dfde3512) | 完善发布商品流程 |
| 2026-11-12 21:50 | [45ee5b4](https://github.com/zhinianboke/xianyu-auto-reply/commit/45ee5b41a61fbdb3bf0b7bf0105bf64c715d58ac) | fix(auth): 注册接口补上「允许用户注册」开关校验 (#369) |
| 2026-11-09 21:45 | [dcb8b74](https://github.com/zhinianboke/xianyu-auto-reply/commit/dcb8b7467b30d5776b18f53663831fc3c9d83034) | 完善自动回复 |
| 2026-11-09 20:50 | [ba52d96](https://github.com/zhinianboke/xianyu-auto-reply/commit/ba52d9638157e8a3503619a05ab4df42ef7e8480) | Update api_keyword_handler.py |
| 2026-11-09 20:30 | [d7c56ea](https://github.com/zhinianboke/xianyu-auto-reply/commit/d7c56eae5ad3e8d9129a9d75fb007670e0d080b0) | 优化自动回复 |
| 2026-11-09 16:42 | [c1d92fc](https://github.com/zhinianboke/xianyu-auto-reply/commit/c1d92fcac2f2a3a11dc8adb9b519d6eae656e451) | 完善用户管理 |
| 2026-11-08 22:41 | [ceaa57e](https://github.com/zhinianboke/xianyu-auto-reply/commit/ceaa57ef4aa37d033eaf68a39821916219b11437) | 完善用户管理 |
| 2026-11-08 22:02 | [e4437fd](https://github.com/zhinianboke/xianyu-auto-reply/commit/e4437fd51e5e0f1415f3343bc1bfd112ba05573c) | 完善在线安装 |
| 2026-11-08 21:16 | [6786068](https://github.com/zhinianboke/xianyu-auto-reply/commit/6786068a4de142bd5a41bb038eef4b0658c2fb6d) | 完善在线安装 |
| 2026-11-08 10:34 | [8a5a94f](https://github.com/zhinianboke/xianyu-auto-reply/commit/8a5a94f1021b48993b66021d9f3ce6897e290b40) | 完善在线安装 |
| 2026-11-07 20:37 | [76c0426](https://github.com/zhinianboke/xianyu-auto-reply/commit/76c0426942f8402c461d4cbf805a2155cc1dfb0a) | Update Home.tsx |
| 2026-11-07 19:46 | [a62194e](https://github.com/zhinianboke/xianyu-auto-reply/commit/a62194eed707dbe0362b0a0d9a8cd1763c8aa5b7) | 增加在线安装 |
| 2026-11-07 18:56 | [4af9440](https://github.com/zhinianboke/xianyu-auto-reply/commit/4af9440d0a081c591fd249f0c04d06bfd82a3c4a) | 增加在线安装功能 |
| 2026-11-06 18:59 | [e979e64](https://github.com/zhinianboke/xianyu-auto-reply/commit/e979e643592abff662e3c7c08e01957623b6b81e) | 新增自动回复类型 |
| 2026-11-06 11:28 | [b4222ff](https://github.com/zhinianboke/xianyu-auto-reply/commit/b4222ff4ca52a20f9caced1f617298ec0a8cf79b) | 优化商品查询的速度 |
| 2026-11-04 00:17 | [4d5e2b8](https://github.com/zhinianboke/xianyu-auto-reply/commit/4d5e2b8705af7b7e1aff2fd941ded13bb8fb2106) | 完善码支付 |
| 2026-11-03 08:21 | [f203a47](https://github.com/zhinianboke/xianyu-auto-reply/commit/f203a477f1765f3de3217a4e7840ff961c98d979) | 支付方式支持码支付 |
| 2026-11-03 07:13 | [e1cdb07](https://github.com/zhinianboke/xianyu-auto-reply/commit/e1cdb073e85231d8b9e17497f88f03d7c0bf27be) | 新增定时任务 |
| 2026-11-02 21:49 | [f96d7d9](https://github.com/zhinianboke/xianyu-auto-reply/commit/f96d7d9290fffc7f1de90d7ef1b0134d769e7d29) | 完善系统功能 (#364) |
| 2026-11-01 08:03 | [888fe41](https://github.com/zhinianboke/xianyu-auto-reply/commit/888fe4164c0a9779065cabf2092c825b47200153) | 完善系统设置 |
| 2026-10-31 17:55 | [6f278bd](https://github.com/zhinianboke/xianyu-auto-reply/commit/6f278bdd97f3f71d375a2c0b67ee714a01389234) | 调整系统设置前端样式 |
| 2026-10-31 12:40 | [3dbe45b](https://github.com/zhinianboke/xianyu-auto-reply/commit/3dbe45bae153926160ffef92537f13b878952363) | 首页增加广告 |
| 2026-10-30 19:24 | [7a9cbdd](https://github.com/zhinianboke/xianyu-auto-reply/commit/7a9cbddcc6f7d8a061754c5bd889a62aa85b0e37) | 个人设置支持配置token获取方式 |
| 2026-10-30 15:00 | [5c06cbc](https://github.com/zhinianboke/xianyu-auto-reply/commit/5c06cbc21040e8a61c34a0c4635a5d1b839d2c8c) | token获取方式支持普通用户配置 |
| 2026-10-26 14:03 | [a58dea0](https://github.com/zhinianboke/xianyu-auto-reply/commit/a58dea0d8ef43d4f467f09b5607af4821710700a) | 修改接口 |
| 2026-10-25 22:54 | [8655c91](https://github.com/zhinianboke/xianyu-auto-reply/commit/8655c915fca1fedbcf6bb4bb4c65521d292d49b9) | 新增公开接口 |

原始比较元数据与范围：`data/ops-runs/upstream-review-20261114/evidence.json`。本轮结论为源码核对，不代表已完成运行测试、合并冲突验证或生产验收。
