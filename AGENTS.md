# MaoYuOps（猫鱼运营）项目协作规则

适用于本仓库。用户当前任务的明确要求优先；保留已有和无关修改。

## 项目名称与兼容

- 产品名为 MaoYuOps（猫鱼运营），GitHub 仓库为 `guwangxian-ai/maoyu-ops`，基于 `zhinianboke/xianyu-auto-reply` 二开。保留上游来源、提交历史和现有许可证。
- 现有本地目录、服务器 `/srv/xianyu-auto-reply`、URL 子路径、Compose 标识、数据卷、数据库、MCP 配置和 `xianyu-operations` Skill 名称保持兼容；不能全局替换技术标识或迁移运行数据。详见[更名说明](docs/maoyu-ops-branding.md)。

## 开发与验证

- 当前约定（2026-10-13）：本地开发，部署到云端本项目后验证。本地不启动功能验收、不跑测试套件；可完成必要的语法/类型检查与构建。需要调整此流程时先遵循用户的新指示。
- 技术栈、依赖和目录沿用 [README.md](README.md)。后端为 FastAPI，入口 `backend-web/main.py`；前端为 React + TypeScript + Vite，使用 `frontend/package-lock.json` 和 npm。
- 修改前端时，在 `frontend` 目录执行 `PUBLIC_BASE_PATH=/xianyu-auto-reply/ npm run build` 生成服务器子路径构建；仅后端或文档变化不重复构建无关前端。其他构建及部署命令沿用 [腾讯云部署说明](deploy/tencent-hermes/README.md)。
- 功能、API、Skills 和 MCP 验证使用云端本项目；测试套件如需运行，放到服务器上的隔离环境，不能以生产业务库作为测试数据库。默认只读验证，测试密钥及临时资源按本轮授权创建、撤销和清理。
- 真实发布、平台修改/下架/删除、网盘分享、买家消息和发货继续按用户已授权范围执行；只读测试不包含这些操作。需要扫码、验证码或账户确认时由用户参与。

## 部署与备份

- 每次部署必须有当前任务或上下文中的明确授权；此流程不构成后续任意部署或持续后台任务的授权。
- **切换前先备份**本项目当前源码、站点、数据库和回滚所需镜像；保留本项目私有部署配置，涉及 Nginx 变更时备份本项目 include。确认备份命令成功、归档和数据库备份可读取、原镜像 ID 与回滚标签可用，并记录备份目录、版本和回滚方式。备份失败或完整性未确认时不覆盖线上文件、不切换容器。
- 腾讯云为共享服务器，只更新 `/srv/xianyu-auto-reply`、本项目 Compose 服务及必要的项目专属 Nginx include；不修改其他项目、全局 Docker/网络设置或清理其资源。不运行仓库根目录 `deploy.sh`、`update.sh` 或默认 Compose。
- 保留云端数据库、授权与资料卷；部署包排除本地凭证、私有运行数据和 `.env`，不把本地账号复制到服务器。不得在输出或文档中记录密钥。
- 切换后验证本项目健康、页面/资源、公开接入资料和授权边界，并检查其他项目入口保持正常；失败按本次备份回退，不能把“已上传/已构建”报告为验证完成。

## 入口与记录

- 项目入口：<https://43.136.91.22/xianyu-auto-reply/>。
- [腾讯云部署说明](deploy/tencent-hermes/README.md)：隔离边界、现有命令、部署与回退流程。
- [部署记录](docs/tencent-hermes-deployment.json)：以当前实际版本、备份路径、回滚方式及真实验证结果为准，完成部署后更新；不覆盖历史事实为未经执行的成功。
- [AI 接入中心](docs/ai-access-center.md)：Skills 下载、远程 MCP、独立密钥与权限。验证时优先使用服务器发布的说明和技能包，凭证仅进入私有客户端配置。

## 当前 AI 客户端

- 本机已安装 `xianyu-operations`；服务器包与本机修订状态见 [运营技能文档](docs/skills-operations.md)。更新时核对实例、版本和已有修订，避免覆盖尚未随服务器包发布的修复。
- Codex MCP 名称 `xianyu`，实际连接 `https://43.136.91.22/xianyu-auto-reply/mcp`。当前密钥保留四域 read；原 `drive:write`、`delivery:write` 已等价展开为网盘及卡券/订单细分写权限，能力范围不变，未增加闲鱼账号启停、消息发送或删除权限。原密钥有效至 2027-01-12 20:31:53（北京时间）。实时权限以 `mcp_capabilities` 为准；接口权限不替代每项业务的经营授权。
- 使用 Skill 脚本时显式传 `--config /Users/maowangxian/.codex/private/xianyu-cloud/mcp-client.json`。原生工具未加载时可用该脚本连接云端，不能回退到历史本地默认配置。只读接入结果与平台账号是否就绪分别报告。

- 2026-10-14 已部署 Skills `2026.10.14.1` 并同步本机。MCP 写入必填稳定 `operation_id`；断线先用 `resume` / `mcp_operation_get`，不能换号重发。服务端 `observed` 只表示响应已保存，异步任务仍要核验；历史操作查原本地 ledger。

- 2026-10-16 按用户的自动销售聊天配置授权，为既有云端密钥增加 `messages:settings:write` 和 `xianyu:items:write`，用于 AI 规则及商品话术配置；原密钥和到期时间保持不变。AI 服务商凭证不进入 MCP 参数、源码或运营文档，按明确授权通过既有 SSH 和配置服务私有保存；开关、话术、网盘分享及卡券仍通过 MCP 留存回执。
- 自动回复引擎必须保留明确配置的数值 `0`，不能用 `value or default` 把零优惠、零议价轮数恢复成默认额度。对应云端隔离回归入口为 `websocket/tests/test_ai_reply_settings.py`；本地仍只做语法检查，发布与结果见部署记录。
