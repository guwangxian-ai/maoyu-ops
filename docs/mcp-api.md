# 闲鱼与网盘 MCP

## 当前实现

当前使用云端 `https://43.136.91.22/xianyu-auto-reply/mcp`，使用官方 Python MCP SDK 的 **Streamable HTTP**。MCP 与云端网页调用相同的业务接口、数据库、网盘授权和任务实现。网页入口为 `https://43.136.91.22/xianyu-auto-reply/`。本地开发、备份后部署和云端验证流程见 [项目规则](../AGENTS.md)。

完整业务权限下共 **245 个业务工具和 3 个 MCP 辅助工具**。`tools/list` 按令牌权限过滤；实际名称、参数和说明以发现结果为准。新增 HTTP 路由不会自动加入 MCP，需要在 `backend-web/app/mcp/catalog.py` 中明确登记。

已提供 [AI 接入中心](ai-access-center.md) 和可下载的运营 Skills，服务器版本与验证状态见 [部署回执](tencent-hermes-deployment.json)。MCP 负责执行能力；[闲鱼运营 Skill](skills-operations.md) 提供同步、选品、上架、资料同步分享和卡券发货流程；连接本身不会启用定时运营、自动消费或无人值守发布。

## 已封装的业务范围

| 业务 | 能力 | 代表工具 |
| --- | --- | --- |
| 闲鱼账号 | 本人账号列表、状态、统计、发货/回复配置、发布能力 | `account_list_cookie_options`、`account_get_publish_account_capability` |
| 商品和选品 | 搜索、同步、本地记录维护、鱼小铺平台编辑/改价、平台下架/删除 | `search_search_items`、`items_list_items_paginated`、`items_batch_offline_items` |
| 上架 | 分类推荐、素材、图片/视频上传、地址、单个/批量发布、日志、AI 上架任务 | `publish_publish_single`、`publish_publish_batch`、`ai_listing_create_task` |
| 消息和回复 | 会话、文本/图片消息、撤回、快捷语、关键词、默认回复、商品回复、AI 回复设置 | `chat_get_conversations`、`chat_send_message`、`chat_image_send_image` |
| 订单和卡券 | 订单拉取、详情、发货、取消、评价配置、卡券及本人商品关联 | `orders_manual_delivery`、`cards_create_card`、`cards_update_card_items` |
| 夸克 | 授权状态/会话、文件/搜索、上传/下载、新建目录、改名、移动、转存、分享、异步任务 | `quark_files`、`quark_upload`、`quark_create_share`、`quark_job` |
| 百度 | 授权状态/扫码会话、文件/搜索、上传/下载、新建目录、移动、复制、改名、删除、分享/取消分享 | `baidu_files`、`baidu_upload`、`baidu_download`、`baidu_create_share` |
| 多网盘资料 | 上传、URL/网盘/分享导入、分析、版本、同步、分享、重试、生成发货卡券 | `assets_upload`、`assets_sync`、`assets_shares`、`assets_card` |

不向 MCP 暴露系统管理、数据库管理、原始 Cookie/密码、AI 供应商密钥管理、MCP 令牌管理和返佣子系统等接口。卡券关联限制为本人卡券和本人商品；涉及对接卡券的关联继续使用现有页面。平台本身不支持的能力不会用模拟结果补齐：夸克目前不能删除云端文件，也不能枚举全部历史分享；先读取对应 `capabilities`。

**商品查询的新旧数据边界：** `items_list_items*` 和 `items_get_item_detail` 读取本项目数据库。查询闲鱼当前情况时，先用 `items_fetch_all_items_from_account` 同步相应账号并检查完成结果；传 `body.cookie_id` 是「获取商品」，省略则是「获取所有账号商品」。这类同步属 `xianyu:read`，会更新系统副本，不改写闲鱼平台商品。同步只覆盖在售，未返回的本地旧记录可能保留；`success=true` 也可能包含跳过或部分账号失败。不能仅凭本地存在/缺失判断平台状态，详细顺序见 [技能同步规则](../skills/xianyu-operations/references/catalog-sync.md)。

商品操作的工具映射、账号限制与失败恢复见 [商品编辑、下架和删除](../skills/xianyu-operations/references/listing.md)。`items_update_item` 是本地修改；`items_delete_item_unified` / `items_delete_item` / `items_batch_delete_items` 是本地删除并清理卡券关联，均不会修改闲鱼商品状态。平台下架与平台删除是不同工具；后者还清理平台成功项的本地记录。鱼小铺平台原商品编辑与“下架后发布新商品”也不能混用。

## 当前 AI 客户端接入

本机 Codex 已配置名为 `xianyu` 的 MCP，通过 stdio 适配连接上述云端 HTTPS 地址。服务器发布版 `xianyu-operations` 已安装，当前会话原生 MCP 调用也已验证成功。

- 私有配置：`~/.codex/private/xianyu-cloud/mcp-client.json`，权限 `0600`，保存云端地址及独立令牌；不要提交或分享文件内容。
- stdio 适配：`~/.codex/private/xianyu-cloud/xianyu_mcp_client.py`，由 Codex 拉起，使用项目既有 Python 环境；不同客户端也可使用接入中心提供的配置模板。
- 当前令牌仅有 `xianyu:read`、`drive:read`、`delivery:read`、`messages:read`，有效至 2026-11-12 15:23:41（北京时间）。实际发现 101 个只读工具，写入请求由服务端拒绝。增加业务权限必须有相应授权。

历史 `data/local-dev/mcp-client.json` 和 `mcp-stdio.json` 指向本地开发环境，不用于当前云端验收。历史本地验证记录保留在 `mcp-verification.md`，当前结果见 [运营技能文档](skills-operations.md)。

在仓库根目录运行：

```bash
# 列出工具及其真实参数定义
data/local-dev/venv/bin/python scripts/xianyu_mcp_client.py \
  --config ~/.codex/private/xianyu-cloud/mcp-client.json tools

# 只读查询百度能力
data/local-dev/venv/bin/python scripts/xianyu_mcp_client.py \
  --config ~/.codex/private/xianyu-cloud/mcp-client.json call baidu_capabilities

# 使用 arguments.json 中的 JSON 参数调用工具
data/local-dev/venv/bin/python scripts/xianyu_mcp_client.py \
  --config ~/.codex/private/xianyu-cloud/mcp-client.json call quark_files --arguments arguments.json

# stdio 服务，供 AI 客户端拉起；标准输出只承载 MCP 协议
data/local-dev/venv/bin/python ~/.codex/private/xianyu-cloud/xianyu_mcp_client.py \
  --config ~/.codex/private/xianyu-cloud/mcp-client.json serve
```

Codex 配置使用 Python、脚本和私有配置的绝对路径，不依赖工作目录。移动仓库或更换电脑后应重新配置 Python 路径与独立密钥。不要同时手工在终端运行 `serve` 并把其输出当作普通文本使用。以上命令在本机执行客户端，但所有业务请求发送到云端，不启动本地业务服务。

支持 Streamable HTTP 且能设置请求头的客户端，也可以直接连接：

```text
URL: https://43.136.91.22/xianyu-auto-reply/mcp
Authorization: Bearer <从私有配置读取的 MCP 令牌>
```

当前采用项目自己的可撤销 Bearer 令牌，**没有实现 OAuth 授权发现或动态客户端注册**。只支持 OAuth 连接、不能配置请求头或 stdio 的客户端暂不兼容。不要将网站 JWT 当成 MCP 令牌。

## 创建和管理令牌

为新的 AI 客户端单独生成令牌；优先在云端 [AI 接入中心](https://43.136.91.22/xianyu-auto-reply/ai-access) 创建、验证和管理。默认仅有四个域的读取权限。CLI 也支持交互初始化：

```bash
data/local-dev/venv/bin/python scripts/xianyu_mcp_client.py \
  --config ~/.codex/private/xianyu-cloud/another-mcp-client.json init \
  --url https://43.136.91.22/xianyu-auto-reply --username admin --name "AI client" --days 30
```

CLI 交互输入网站密码，不将密码放在命令参数、标准输出或配置中。若本站登录需要验证码，先在网页完成人工登录，再通过 `--website-token-file` 提供一个仅包含本人网站 JWT 的私有文件。CLI 不会查找浏览器或其他工具的认证材料。

`--scopes` 可以指定所需权限，例如 `--scopes drive:read drive:share:write delivery:read delivery:cards:write`。有效期为 1–90 天。已有配置不会被覆盖，配置保存失败时会尝试撤销刚创建的令牌。

读取仍为四域 `:read`；写入按能力细分，例如 `drive:share:write`、`delivery:cards:write`、`delivery:orders:write`、`xianyu:account-status:write`。完整列表由 `mcp_capabilities.scope_options` 返回；旧四域 write 仅为兼容保留，管理页面提供细分选项。删除独立使用各域 `:delete`。

准确 scope 见工具说明；例如撤回聊天消息是 `messages:send:write`，撤销网盘授权是 `drive:delete`。每个令牌绑定一个本站用户，即使由管理员创建，也不会继承管理员跨用户数据访问权。

以下管理接口使用**网站 JWT**，不作为 MCP 工具暴露：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/v1/mcp/catalog` | 完整业务工具目录、JSON Schema 和权限 |
| GET | `/api/v1/mcp/tokens` | 列出本人令牌元数据，不能重新读取明文 |
| POST | `/api/v1/mcp/tokens` | 创建；`name`、`scopes`、`expires_in_days` |
| DELETE | `/api/v1/mcp/tokens/{token_id}` | 撤销本人令牌；后续请求立即失效 |
| GET | `/api/v1/mcp/logs?page=1&page_size=30` | 本人调用回执 |

数据库保存令牌哈希。调用审计记录令牌编号、工具名、成功状态、耗时和错误类型，不保存参数/响应；独立的写操作回执保存参数摘要及脱敏业务响应用于恢复。原有业务服务的日志策略维持原项目行为。

## 参数、文件与异步任务

业务工具将 HTTP 参数放在 `path`、`query`、`body` 中，不接受客户端指定任意 HTTP 地址、请求头或服务器文件路径。只读查询示例：

```json
{"query":{"parent_fid":"0"}}
```

上面是 `quark_files` 的参数。百度根目录使用 `baidu_files` 和 `{"query":{"dir":"/"}}`。夸克文件接口没有 `page_size` 参数，不要直接套用百度参数。

所有业务写入/删除需要顶层 `"confirm": true` 和稳定的 `operation_id`，缺少时在调用业务服务前拒绝。它表示用户已批准该操作，或已经批准覆盖该操作的运营规则。原接口需要 `body.confirmed` 时仍须提供：

```json
{
  "body": {"dir":"/", "name":"示例目录", "confirmed":true},
  "confirm":true,
  "operation_id":"example:operation:one"
}
```

上例用于 `baidu_create_folder`。请先取得真实授权，再执行外部写入。

### 文件传输

1. 小文件用 `mcp_upload_file`，传 `filename`、`content_base64` 、`confirm:true`、`operation_id` 和可选 `content_type`，原始文件最多 2 MiB。
2. 大文件用 `POST /api/v1/mcp/files`，Bearer 身份认证及 `X-MCP-Operation-Id` 请求头，multipart 字段 `file`，单文件最多 256 MiB。
3. 获得 `upload_id` 后，业务上传工具接收 `{"upload_id":"..."}`；多文件字段用此对象的数组。例如 `baidu_upload`：

```json
{
  "body": {"files":[{"upload_id":"实际返回的32位文件编号"}],"dir":"/目标目录"},
  "confirm":true,
  "operation_id":"example:operation:one"
}
```

4. 下载工具返回私有文件句柄；`mcp_read_file` 按 `offset`、`limit` 读取 base64，单块最多 128 KiB。也可携带 Bearer 请求返回的 `download_path`。

暂存文件按本站用户隔离，24 小时到期，下一次写入清理过期文件；用户暂存限额 1 GiB。限额针对单进程写入检查，当前本地后端是单进程；高并发多进程部署前应补充跨进程配额协调。提供句柄不等于永久保存文件，真正资料应通过网盘或资料工具接收。

超过 1 MiB 的 JSON 工具结果也保存为私有文件，返回读取句柄，避免默默截断。商品图片/视频使用上传工具返回的 `/static/uploads/` 路径或允许的闲鱼/阿里 HTTPS CDN 地址，不能指定服务器任意路径。

### 多网盘分享并生成卡券

```text
mcp_upload_file / HTTP 上传
  → assets_upload（选 quark 和 baidu）
  → assets_sync → assets_get 查询到 ready
  → assets_shares → assets_get 查询到 deliverable
  → assets_card（可选择关联本人商品）
  → cards_get_card 检查同时包含两个平台的链接和提取码
```

`assets_upload` 沿用原 multipart 接口，`body.providers` 是 JSON **字符串**：`"[\"quark\",\"baidu\"]"`；同时传 `name`、`confirmed:true`、`files` 和顶层 `confirm:true`。`assets_shares` 通过 `body.period` 指定 0、1、7、30 天。`assets_card` 的 `body.item_ids` 留空时只生成卡券，不绑定在售商品或发送消息。

夸克独立写任务返回任务编号，用 `quark_job` 查询；资料同步用 `assets_get` 查询；商品批量发布用 `publish_get_batch_status`。提交成功不等于平台操作完成。出现 `partial`、`unknown`、`interrupted`、超时或连接中断时，先核对原任务/平台结果，再决定是否重试。本层没有为全部旧接口提供幂等键，不应盲目重复发布、分享或发货。

`deliverable=true` 校验当前发布快照、所需平台、账号绑定信息及有效期，不能证明远端分享从未被人工撤销。扫码、验证码、平台登录验证仍需要人完成。

## 开发与后续部署

- `backend-web/app/mcp/`：协议、白名单目录、鉴权、私有业务适配、旧接口归属检查和文件传输。
- `backend-web/app/api/routes/mcp_access.py`：网站令牌管理和私有文件 HTTP 接口。
- `common/models/mcp_access.py`：令牌与调用记录，按现有数据库初始化机制创建两个表。
- `scripts/xianyu_mcp_client.py`：私有配置、手动调用和 stdio 适配。
- `backend-web/tests/test_mcp_gateway.py`：官方客户端对真实业务路由的协议及边界测试，外部网盘调用在单元测试中隔离；真实网盘验证单独记录。

复用官方 `mcp>=1.30,<2`（本机 1.30.0）和现有 httpx/FastAPI；增加 `jsonschema>=4.20,<5` 的直接依赖声明，用于服务端参数校验（本机已作为依赖安装）。两者许可证均为 MIT。本轮未调用付费模型或购买服务。

默认只接受本地主机和 Origin；可选环境变量见 `.env.example` 的 `MCP_ALLOWED_HOSTS`、`MCP_ALLOWED_ORIGINS`、`MCP_FILE_DIR`。文件目录必须位于私有持久化存储，不能位于静态资源目录。当前云端已配置 HTTPS、主机白名单、项目子路径代理与持久化存储，带认证的 SDK、Skills 和 Codex 原生调用均已通过。后续部署先核验本项目备份，具体边界和步骤见 [腾讯云部署说明](../deploy/tencent-hermes/README.md)。

历史本地验证记录见 `mcp-verification.md`；2026-10-13 云端只读接入结果见 [运营技能文档](skills-operations.md)。本轮云端实测没有真实上架/下架在售商品、发送买家消息或触发订单发货。云端两种网盘尚未授权，文件读写没有在本轮验收；这些业务仍需补齐账号状态，并按具体运营授权验证。

官方资料：[Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)、[Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)、[MCP 授权规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)。本项目私有令牌接入不是规范中的完整 OAuth 授权服务器。


## 细分能力与持久回执（2026.10.14.1）

日常运营通过 MCP；Skill 管理授权范围、步骤与核验，SSH 用于部署和接入维护。权限列表由 `app/mcp/permissions.py` 集中维护，同时用于发现、执行校验与接入页面。新密钥选择细分能力，四域旧 write 可等价展开，不额外增加 read/delete。重点分开分享、资料操作、卡券配置、真实订单发货、账号启停、平台发布/编辑/下架及买家消息。

所有 MCP 写工具新增必填 `operation_id`；原生客户端须重新发现 schema，Skill 脚本自动注入。HTTP 暂存上传使用 `X-MCP-Operation-Id`。已有查询和网站业务 API 不变。`xy_mcp_operations` 为新增表，不改业务表；数据库唯一键 `(owner_id, operation_id)` 协调不同密钥与进程，参数 SHA-256 防止同编号不同请求。保存脱敏响应，不保存原请求或凭证。

`running → observed/uncertain`：先持久占用，后派发一次；连接取消不会取消已派发任务；心跳失效只标未知，不接管执行。`mcp_operation_get`、`mcp_operations_list` 按本人及当前权限提供只读恢复，撤销/过期的密钥不能查询。`observed` 包含成功或失败响应；异步任务另查状态。未知响应、5xx、持久化失败不应触发重试。相同编号返回原响应，不同参数返回冲突。

这是 MCP 调用去重，不覆盖不同编号、网站请求、原有自动任务或平台内部重试。升级前历史仍查本地 ledger；大响应文件句柄保留原有效期。回执不自动删除，备份和回滚应保留新增历史，不能用旧库覆盖后再重放请求。部署验证及备份路径以部署记录为准。
