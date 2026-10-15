# GitHub 同步记录（2026-10-15）

目标仓库：`guwangxian-ai/xianyu-auto-reply`，分支 `main`。

本地功能快照 `b11857d` 包含网盘、资料卡券、MCP 权限与回执、运营 Skills、共享服务器部署适配及文档。与远程 `7951b21` 合并，保留双方历史，不使用强制推送。

## 合并处理

- 保留 MCP 依赖、账号归属校验与错误脱敏、Redis 并发初始化和取消清理。
- 接入远程聊天历史合并、缺口恢复、首次连接通知与重连退避，保留本地滚动位置、过期响应和跨账号消息保护。
- 数据库连接池保留远程默认容量，允许显式设置正数的小连接池；共享服务器现有 `DB_POOL_SIZE=5` 可正常通过配置校验。
- 保留后端回归测试的 Git 跟踪入口，凭证、网盘授权、私有配置和运行数据仍排除在版本管理之外。

## 合并后验证

- 本地 TypeScript 检查及 `PUBLIC_BASE_PATH=/xianyu-auto-reply/ npm run build` 通过；Python、JSON、TOML 和相关 Shell 语法检查通过。
- 服务器隔离容器：71 项后端测试通过，覆盖 Redis、聊天账号隔离、MCP 权限、回执恢复、接入包、Skills 和部署配置。
- 服务器隔离容器：18 项聊天检查通过，覆盖消息去重、分页恢复、中断与过期响应、WebSocket 账号隔离及滚动位置。
- 测试容器禁用网络，不挂载生产凭证或数据卷，执行后自动删除；生产容器 ID 和重启次数保持不变。
- 提交范围检查未发现已知本地/云端 MCP 密钥、私钥或运行数据。第三方源码保留原有许可证和来源记录。

完整测试证据保存在私有目录 `data/local-dev/github-sync-20261015/`；云端记录位于 `/srv/xianyu-auto-reply/releases/github-sync-20261015-b11857d/verification.json`。

本次仅更新 GitHub 源码，未重新部署生产服务。线上发布记录仍以 `docs/tencent-hermes-deployment.json` 为准。本次未执行真实买家消息、订单发货或浏览器交互验收，新增费用为 0。
