# 执行与恢复

## 连接

项目 MCP 使用 Streamable HTTP 和私有 Bearer，或项目 stdio 适配。当前不是 OAuth 自动登录。沿用用户提供/项目已生成的私有配置，内容为 `url` 和 `token` 等字段；不要扫描浏览器、历史脚本或其他项目寻找凭证。

从接入中心下载的技能包先读包内 `CONNECT.md` 和该实例的接入说明，沿用其中的服务器地址。优先复用该实例已有的私有配置；首次接入且没有可用凭证时，AI 接入中心是标准签发入口。原生 MCP 客户端可直接配置连接；使用脚本时需要 Python 3.11+，复用已有运行时或在独立虚拟环境安装包内 `requirements.txt`。使用绝对路径 `--config` 或 `XIANYU_MCP_CONFIG` 指定私有配置。不能把模板占位符当成密钥，也不能把远端地址改成本机。

在项目源码目录开发时，可复用 `data/local-dev/venv/bin/python`；脚本历史默认配置 `data/local-dev/mcp-client.json` 可能属于本地开发环境，操作部署实例时必须显式指定云端配置，不能因为本地密钥权限较多就替换服务器。源码没有 `CONNECT.md` 时参考项目 `docs/mcp-api.md`。下载包之外的客户端配置和运行回执放在私有目录，不能写回公开技能源文件。

### 权限与接入恢复

`mcp_capabilities` 返回的 scopes 和当前 `tools/list` 是权限依据；配置文件里的 scopes 备注可能过时。`doctor` 成功只代表所查接口可调用，不能证明具备写入权限。常见任务需要：

| 任务 | 所需权限 |
| --- | --- |
| 查询/同步自己的商品 | `xianyu:read` |
| 查询网盘、核对分享 | `drive:read` |
| 创建网盘分享 | `drive:share:write` |
| 查询卡券和商品关联 | `delivery:read` |
| 创建、修改、绑定卡券 | `delivery:cards:write` |
| 真实订单发货 | `delivery:orders:write` |
| 账号启停 | `xianyu:account-status:write` |
| 发布 / 编辑 / 下架商品 | `xianyu:publish:write` / `xianyu:items:write` / `xianyu:offline:write` |
| 上传文件 / 同步资料 | `drive:files:write` / `drive:assets:write` |
| 发送买家消息 | `messages:send:write` |

新密钥按能力选择；旧的四域 `*:write` 保持兼容，可等价展开为该域的细分写权限，不隐含 read/delete 或其他业务域权限。卡券权限不授予订单发货或账号启停。具体工具仍以实时 schema 的 Required scope 为准。工具缺失时先核对同一服务器的配置、有效期和 scopes；401 是认证失效，403 或按权限过滤后的工具缺失不能解释为网盘未登录。权限调整后重新发现工具；原生客户端目录尚未刷新时可用脚本重新连接同一 MCP。

已有可用 MCP 连接不需要再次登录后台。用户要求通过 Skill/MCP 执行业务时，不因权限缺失改用浏览器写入或直接改业务数据库。缺少必要授权时明确提出所缺 scope；已明确授权的范围不重复询问，用户要求长期保留时不自行改成任务后撤销。

用户另行明确授权通过既有 SSH 管理这台服务器的 MCP 凭证或权限时，可以在该授权范围内使用项目管理实现并保留原权限备份；这属于接入管理，分享、商品、卡券和发货仍通过服务器 MCP 执行。服务器通常只存密钥哈希，不能声称能从中恢复原始密钥。优先复用已保存的同实例凭证；新签发、扩大权限或延长期限按已有授权分别判断，不擅自扩大到其他业务域、删除权限或修改安全期限上限。真实 CAPTCHA、验证码、扫码仍由用户完成。

示例中 `python` 表示上述可用的项目 Python，脚本路径在项目根目录为 `skills/xianyu-operations/scripts/mcp_ops.py`。技能单独安装时使用技能目录中的绝对脚本路径。

```bash
python skills/xianyu-operations/scripts/mcp_ops.py doctor
python skills/xianyu-operations/scripts/mcp_ops.py schema \
  --tool search_search_items --tool publish_publish_single
python skills/xianyu-operations/scripts/mcp_ops.py \
  --output data/ops-runs/current/search.json \
  call search_search_items --arguments data/ops-runs/current/search-args.json
```

`doctor` 只检查连接、账号列表、网盘状态和能力；工具调用成功不等于已登录、账号能发布或订单能发货。继续检查返回字段和阶段条件。

## 参数与回执

- 业务工具名和 schema 以当前 `tools/list` 为准；参数分 `path/query/body`，没有通用 `url`、HTTP 请求或服务器文件路径透传。
- 服务端回执版本 1 的写入必须有顶层 `confirm:true` 和 `operation_id`；脚本从 `--operation-id` 自动注入。原接口要求时还要 `body.confirmed:true`。它们表达已有授权，不生成授权。
- 工具的 `readOnlyHint` 为真才按只读处理；无此标记按写入处理。`mcp_upload_file` 是暂存写入。
- 原生 MCP 返回的业务对象通常为 `{success,message,data}`；脚本包装为 `{ok,result:{success,message,data}}`。`ok` 仅代表本次调用返回成功。
- 脚本的 `observed_at` 是本机收到响应的 UTC 时间，不是平台事件时间；回放旧回执时保留原时间。旧回执缺少时间时标为未知，不能用阅读时间伪造采集时间。
- 大响应可能返回私有文件句柄。按 `mcp_read_file` 的真实 schema 分块读取，不能把句柄当结果或截断数据后声称查全。

脚本写入默认只校验参数并预览；不会调用业务写工具。执行示例：

```bash
python skills/xianyu-operations/scripts/mcp_ops.py \
  --ledger data/ops-runs/receipts.sqlite3 \
  --output data/ops-runs/current/sync.json \
  call assets_sync --arguments data/ops-runs/current/sync-args.json \
  --execute --operation-id 'run-20261012:asset-v1:sync' \
  --authorization '当前会话已批准的独立测试目录与分享测试'
```

同一服务器、同一本站用户的所有客户端共用服务端操作编号，即使换机器、换密钥也不能重复执行。私有 ledger 保留本机授权引用和缓存，能共用时继续共用。操作编号绑定具体业务意图，如 `account:item-version:publish`、`asset:version:share`、`account:order:deliver`；不能为重试随机换号。

| 状态 | 含义与动作 |
| --- | --- |
| `observed` | 已收到接口响应；同编号同参数返回旧回执，不再次写入。仍需查询异步任务及业务结果。业务失败也保留原响应。 |
| `running` | 服务端已占用编号，仍在执行；客户端断开不取消已派发操作，继续查询原编号。 |
| `uncertain` | 异常、进程终止或心跳过期，结果未知；只读核对，不自动接管或重发。 |

```bash
# 下列命令均只读；连接云端时补充 --config /私有目录/mcp-client.json
python skills/xianyu-operations/scripts/mcp_ops.py resume 'run-20261012:asset-v1:sync'
python skills/xianyu-operations/scripts/mcp_ops.py operations --limit 30
python skills/xianyu-operations/scripts/mcp_ops.py \
  --ledger data/ops-runs/receipts.sqlite3 receipt 'run-20261012:asset-v1:sync'
```

`resume` 调用 `mcp_operation_get`，返回原业务响应、状态和服务端时间；`operations` 调用 `mcp_operations_list`。原生 MCP 同样使用这两个工具。旧服务器无此能力时只能查询本地 `receipt`；升级前的操作不会凭空出现在服务端历史中。旧回执需保留，不因服务器查不到就重做。

先查服务端回执，再查其 `batch_id/asset_id/job_id/item_id/order_no` 及发布日志、订单/平台记录。业务已完成就恢复后续步骤；仍在执行就继续查；证明未执行且原执行进程已结束后，记录证据，在已有授权范围内显式创建新的操作编号。没有证据时保持未知。脚本没有清锁或强制重发命令。任务编号响应的 `observed` 不证明后台任务成功，也不代表买家已收到。

服务端以用户和操作编号的数据库唯一键占用，再保存参数摘要与脱敏结果；同编号不同参数返回 `OPERATION_CONFLICT`，不执行。回放带 `mcp_operation.replayed=true` 并保留原完成时间。回执持久化失败返回 `RECEIPT_PERSISTENCE_UNCERTAIN`，即使业务结果成功也只能查询核对。进程重启后过期租约转为未知，不会自动重发。

这保护的是同一编号的 MCP 派发。不同编号、直接网站操作、原有自动任务和平台内部重试不共享此约束，不能宣称所有外部业务“恰好一次”。异步任务中断后的恢复还要遵守对应业务工具的规则。大结果中的临时文件句柄仍有有效期。

外部写入优先用脚本保存授权与私有证据；原生 MCP 可直接使用相同 `operation_id` 和 `confirm`，获得同一服务端保护。已在本地登记为中断的脚本调用只查询服务端回执，不再次发写请求；换密钥导致本地身份不匹配时直接用 `resume` 查询，不能删除 ledger 解锁。

## 文件与运行记录

```bash
python skills/xianyu-operations/scripts/mcp_ops.py \
  --output data/ops-runs/current/upload.json \
  upload --file /实际路径/自有资料.zip \
  --execute --operation-id 'run-20261012:source:upload' \
  --authorization '当前会话已批准上传此自有资料'
```

脚本使用既有 `/api/v1/mcp/files`，自动携带 `X-MCP-Operation-Id` 请求头，单文件最多 256 MiB，返回 `upload_id`；大于限制时明确报告，不能静默截断。暂存不是云端同步，24 小时后可能失效。同编号旧回执不会重新上传；确已过期时记录原因再创建新操作。

创建运行目录 `data/ops-runs/<run-id>/`（目录 0700、文件 0600，项目已忽略 `data/`）。按实际需要保存：

- `plan.json`：目标、用户授权引用、账号/商品/订单范围、价格/数量、资料来源和许可、必要网盘、承诺有效期、费用上限、阶段状态。
- 草稿、工具参数、只读证据和写入回执，含检查时间及真实业务编号。
- 同步/发布/发货未完成时写清 `blocked_reason` 和下一次只读核对步骤。

不在技能源码写具体账号、Cookie、token、买家信息、链接提取码或本次选品内容。`--output` 可避免把完整业务数据打印到终端；只在用户需要时展示对应结果。
