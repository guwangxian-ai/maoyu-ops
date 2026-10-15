# 闲鱼运营 Skill

## 当前接入与源码

技能源码在 [`skills/xianyu-operations/SKILL.md`](../skills/xianyu-operations/SKILL.md)。一个主技能按需加载选品、上架、多网盘资料/卡券、订单发货四个流程，通过现有 [MCP](mcp-api.md) 调用真实业务接口。

本机发现目录 `~/.codex/skills/xianyu-operations` 已安装服务器发布的完整技能包，当前版本 `2026.10.14.1`。原开发目录链接已备份到 `~/.codex/skill-backups/xianyu-operations/20261013T060644Z/development-link`，源码继续留在本仓库，修改源码不会自动改变已安装的服务器版本。后续部署后从服务器接入中心更新技能包并核验 SHA-256。换电脑需要单独配置 MCP，不复制账号凭证或私有运行记录。

当前开发流程：本地开发及必要构建，部署前先完成并核验备份，功能验证在云端本项目进行，详见 [项目规则](../AGENTS.md)。2026-10-13 已安装服务器技能包并完成带认证的云端只读实测。Codex MCP 名为 `xianyu`，使用本机 stdio 适配连接服务器 HTTPS，密钥单独保存在私有配置。首次接入时为四域只读、101 个工具；2026-10-14 的权限与期限调整见下文。

### 使用方式

对可加载该技能、并能连接本项目 MCP 的 AI，可以直接提出任务，例如：

> 使用 $xianyu-operations，先帮我研究适合自制的数字资料商品，给出有证据的推荐和一份待发布草稿。

> 使用 $xianyu-operations，将我指定的自有资料同步到夸克和百度，检查分享有效期并准备发货卡券；沿用本次已批准的资料范围。

> 使用 $xianyu-operations，检查指定订单和卡券状态，给出发货预览。

跨 AI 客户端可以复制整个技能目录；仍需客户端支持技能指令，以及可用的 MCP 或 Python 执行入口。凭证单独配置，不随技能复制。该技能不包含调度器，不会自动开始经营；长期运行需另行定义经营范围与调度。

## 调用脚本

### 2026-10-14 本机接入修订

已读取服务器 `/ai` 和 `/ai/guide.md`，本机安装仍基于服务器 `2026.10.13.2` 包，并同步了项目中的三份技能文档修订：先核对同一实例的实时 scopes；区分首次凭证签发、权限不足和网盘登录；用户指定 MCP 时，分享及卡券操作继续使用 Skill 脚本调用 MCP。用户明确授权的 SSH 只用于接入管理，不直接改业务数据库。

已有网盘文件夹新增直接分享→固定文本卡券→现有商品绑定的流程；跨盘复制和资料版本管理仍使用 `assets_*`。卡券配置成功与发货账号在线分别核验。本机 `CONNECT.md` 已同步说明，项目源及安装目录均通过 Skill 校验；未修改业务代码、未重新部署服务器公开技能包。后续发布应保留这些已验证修订，不能用旧包静默覆盖。

用户已批准现有云端密钥长期保留 `drive:write` 和 `delivery:write`，实时 MCP 已确认生效；其余仍为原有四域读取权限，不新增删除或消息发送权限。用户明确确认延长 90 天后，到期时间已更新为 2027-01-12 20:31:53（北京时间），原密钥未更换。持续后台运营或对外操作仍以各任务授权为准。

本次已通过安装的 Skill 脚本在服务器创建用户指定文件夹的永久带提取码分享，创建并启用文本卡券，绑定已有商品；分享内容和卡券关联均由 MCP 读回核验。私有回执见 `data/ops-runs/silkroad-card-20261014/verification.json`。用户随后批准启用账号，已临时增加 `xianyu:write`，通过 MCP 启用并核验真实 WebSocket 在线、自动发货未禁用、卡券关联有效；之后已收回临时权限，保留原长期权限和有效期。回执见同目录 `account-enable-verification.json`。没有向买家发送测试消息，实际订单发货尚未验收。

### 商品查询的数据来源

用户要求查询闲鱼当前商品时，Skill 会先同步对应账号，再结合本次平台返回与本地记录判断；即使本地已有商品，也不能省略数据是否最新的核对。「获取商品」同步指定账号，「获取所有账号商品」同步当前权限范围内全部账号；MCP 使用同一 `items_fetch_all_items_from_account` 工具，以是否传 `body.cookie_id` 区分。

同步失败、被跳过、部分账号失败或入库不完整，均不能被表述为完整同步。现有同步只覆盖在售列表，而且不会统一清除本次未返回的本地旧商品。因此本地存在不证明仍在售，本地不存在也不证明闲鱼不存在。明确只查本地/离线分析时可以不联网，但必须标注平台情况未经核验。

具体查询顺序、参数与结果判定集中维护在 [账号商品同步与查询](../skills/xianyu-operations/references/catalog-sync.md)，上架去重、卡券绑定与订单流程均引用它。本次补充仅更新技能与说明，没有改变业务代码或部署。

### 脚本用法

`skills/xianyu-operations/scripts/mcp_ops.py` 复用官方 MCP Python SDK 和已有 httpx/jsonschema，未增加项目依赖。

| 命令 | 作用 |
| --- | --- |
| `doctor` | 查看本人账号、网盘授权、能力；不是上架/发货就绪承诺 |
| `schema --tool 名称` | 按当前令牌发现所需工具的真实参数 |
| `call 名称 --arguments 文件` | 只读立即执行，写入默认只校验预览 |
| `upload --file 文件` | 通过现有 MCP 私有文件接口暂存，默认预览 |
| `receipt 操作编号` | 查看历史本地回执，不访问网络 |
| `resume 操作编号` | 只读恢复服务端响应与状态 |
| `operations --limit 30` | 查询本人当前权限内的服务端操作历史 |

当前验证目标是服务器 `https://43.136.91.22/xianyu-auto-reply/mcp`。已安装技能入口为 `~/.codex/skills/xianyu-operations/scripts/mcp_ops.py`。云端独立密钥已保存到 `~/.codex/private/xianyu-cloud/mcp-client.json`（0600），有效至 2027-01-12 20:31:53（北京时间）。此配置只连接服务器，不能用历史本地配置替代。

```bash
# <Python> 使用项目既有 data/local-dev/venv/bin/python 的绝对路径；它只是客户端运行时。
<Python> ~/.codex/skills/xianyu-operations/scripts/mcp_ops.py \
  --config ~/.codex/private/xianyu-cloud/mcp-client.json doctor
```

使用 `--config` 或 `XIANYU_MCP_CONFIG` 显式绑定该服务器私有配置。脚本历史默认值 `data/local-dev/mcp-client.json` 指向本地开发环境，当前云端测试不要省略配置参数。服务器技能包 `CONNECT.md` 提供实际接入地址和配置模板；MCP 密钥不写进技能包、文档或聊天。

写入需 `--execute --operation-id ... --authorization ...`，同时满足工具原有 `confirm/confirmed` 参数与权限。授权引用应来自用户当前指令或已批准运营规则，不是 AI 自行授予权限。

回执默认 `data/ops-runs/receipts.sqlite3`，权限 0600。相同操作编号与输入返回已有响应；参数/连接身份不同则拒绝；执行中或中断的操作不能盲目重发。当前服务端也保存同用户共享的持久回执，覆盖其他客户端的相同编号；不覆盖不同编号、网站操作或原有自动任务，不代表外部业务全局恰好一次。

## 本地验证（2026-10-12）

### 本轮真实调用

- 用户登录后，MCP 识别到一个本人闲鱼账号；真实发布能力接口返回鱼小铺及库存/规格支持。
- 修复本机 Playwright 浏览器依赖后，`search_search_items` 实际取得“Excel 模板”商品列表。首次回执发现请求 `page_size=10` 却返回 30 条，已修复服务端返回数量上限；重启后再次实测，返回 10 条，当前抓取页总数 30 条。
- 实时读取夸克、百度授权及能力，复查前次测试资料 `version=published_version=1`、`deliverable=true`，卡券含双平台链接，保持禁用且无商品关联。
- 通过新脚本向本机 MCP 暂存无业务内容文本；读回字节与原文件完全一致，同编号再次调用返回 `replayed=true` 和同一文件句柄，没有再次上传。
- 独立前向测试使用真实搜索与资料回执生成选品/草稿，检查其能区分测试资料、真实商品和待验证假设。

独立测试产物保存在 `data/local-dev/skill-verification/forward-test/`：选品报告、条件式上架草稿、网盘卡券接入说明及执行记录。根据测试反馈补充了离线任务的适用规则、历史图片参数描述的解释，以及新回执的 `observed_at` 时间。原始前向测试报告保留当时证据，后续在线核对和修正单独记录在 `review.json`、`../verification.json`，不改写历史结果。

本轮私有原始证据在 `data/local-dev/skill-verification/`；前次上传→双网盘同步→分享→生成禁用卡券的真实回执在 `data/local-dev/mcp-assets-verification.json`。不能将复查描述为本轮重新执行了全部外部写入。测试分享仅 1 天有效，测试内容不是正式商品。

### 自动检查

已运行 36 项检查：11 项脚本测试、3 项搜索测试、22 项 MCP 协议/权限回归；全部通过。技能通过 `skill-creator` 的 `quick_validate.py`，全部引用文件存在。

```bash
data/local-dev/venv/bin/python -m unittest discover -s backend-web/tests -p 'test_operations_skill.py' -v
data/local-dev/venv/bin/python -m unittest discover -s backend-web/tests -p 'test_search_results.py' -v
data/local-dev/venv/bin/python -m unittest discover -s backend-web/tests -p 'test_mcp_gateway.py' -v
```

检查覆盖写入预览、同编号重复执行、参数/身份变化、异常结果保留、并发占用、私有文件权限、参数校验、搜索错误透传、返回数量和人工验证停止。自动测试使用隔离数据，真实运行证据另存。

## 本轮搜索修复

- 在本项目 `data/local-dev/ms-playwright/` 安装匹配本机 Playwright 1.63.0 的 Chromium Headless Shell，启动脚本设置项目级 `PLAYWRIGHT_BROWSERS_PATH`，不改系统浏览器。不同 Playwright 版本要求匹配的浏览器二进制，依据 [Playwright 官方说明](https://playwright.dev/python/docs/browsers)。
- 搜索保留具体错误，不再在业务失败后连续重开浏览器并返回“未知错误”。
- 每次搜索使用独立临时浏览器目录，结束清理；账号 Cookie 仍从当前本站用户的既有授权读取，避免不同请求共享登录状态。
- 搜索遇到平台验证码时返回 `verification_required`，交给用户人工验证。

## 正式运营前仍需验证

- 真实上架与买家发货未执行：需具体商品、素材/版权、价格/数量、分享承诺，以及账号/订单授权范围，再做一条真实发布和一单受控发货验收。
- 现有订单 `refresh=true` 可能刷新失败后仍返回旧记录；发货前必须取得可靠的当前付款/可发货证据，不能把接口 success 当实时平台状态。
- 发货依赖独立 WebSocket 服务的连接，不能用新聊天管理器的连接状态替代。
- 分享快照有效不保证远端未人工撤销；正式发货按平台支持检查分享可访问性。
- 全自动经营的收益、平台规则、费用和售后需要真实经营反馈验证，本轮没有产生营业收入或新增付费服务费用。

## 云端接入实测（2026-10-13）

- 人工完成登录后，在云端页面新建四域只读密钥；验证有效、刷新后保留元数据，明文区域已关闭。
- 服务器下载的 Skills `doctor` 实际调用云端；官方 SDK 和 Codex 中配置的同一 stdio 适配命令均完成 MCP 初始化、工具发现及只读调用，共 101 个工具。当前会话也已加载原生 `mcp__xianyu` 工具，实际调用 `mcp_capabilities` 成功，返回同样的四域只读权限及商品操作说明。空参数越权请求在服务端返回 `FORBIDDEN`，未进入商品操作。
- 实测 1 个闲鱼账号，系统中为停用状态；能力接口实际返回鱼小铺、支持规格/库存。查询成功不等于自动回复/发货任务已启用。
- 夸克、百度在云端均未授权，因此本轮没有实测网盘文件读写；本机曾授权不能替代云端授权。
- 云端调用记录显示上述成功请求。私有证据位于 `data/ops-runs/cloud-skill-connect-20261013/`；密钥不在证据或技能包中。
- 本轮未重新部署、未运行本地业务测试或测试套件，未修改真实商品、发货或创建分享。


## 服务端恢复与安装同步（2026-10-14，20261014T153000Z）

前述本机接入修订已随 `2026.10.14.1` 正式发布。本机从服务器下载、校验并安装完整包，原安装备份于 `~/.codex/skill-backups/xianyu-operations/20261014T153000Z/previous-installed`；源码、公开包、本机脚本和引用文档已核对。安装版 `doctor` 实连云端通过，共 158 个工具。

旧网盘和发货域写权限等价展开，原密钥与 2027-01-12 20:31:53（北京时间）到期时间保留。卡券配置、真实订单发货、账号启停、分享等独立控制；未授予的账号启停、消息发送、删除请求在服务端拒绝。

`resume` 读取服务端 `running/observed/uncertain` 状态；跨客户端重取同编号响应不重复派发。云端隔离测试 66 项通过，线上对卡券 1 保持原描述的更新从独立账本重复提交，业务审计只有一次；原响应时间、结果及后续只读恢复一致。真实订单未发送，后台异步任务和平台业务状态仍按阶段核验。历史本地回执继续保留。详见 [当前部署记录](tencent-hermes-deployment.json)。
