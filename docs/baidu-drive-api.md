# 百度网盘 API：BaiduPCS-Go

当前页面与 HTTP API 使用维护中的 [qjfoidnh/BaiduPCS-Go](https://github.com/qjfoidnh/BaiduPCS-Go)，固定源码 commit `1b9131817aaf8ca7dee24bc00e33ebc4c7a5cc73`。文件操作和分享执行真实百度接口，不提供模拟账号、文件或成功结果。

## 本地入口

- 页面：`http://127.0.0.1:9000/cloud-drive/baidu`
- API：`http://127.0.0.1:8089/api/v1/cloud-drive/baidu`
- 机器可读合同：`http://127.0.0.1:8089/openapi.json`
- Swagger：`http://127.0.0.1:8089/docs`

所有接口使用项目现有 `Authorization: Bearer <项目访问令牌>`，账号由 JWT 的本站用户决定；请求不接受覆盖 owner/user_id。AI 可通过相同 HTTP 接口调用能力、读取文件、上传和生成分享。扫码确认仍由用户在百度网盘 App 完成。此处提供 HTTP API，未将其声称为已实现的 MCP Server。

本项目统一错误处理可能使用 HTTP 200 返回 `{success:false,code:401,...}`。调用方必须同时检查 HTTP 状态及 `success`；不能将 HTTP 200 当作操作成功。

## 扫码、持久化与退出

1. `POST /auth/start`，请求 `{confirmed:true}`。
2. 在当前页面展示返回的 `qrcode_image`（百度真实 PNG 的 data URL）。
3. 用户使用百度网盘 App 扫码并确认，轮询 `GET /auth/sessions/{session_id}`。
4. 后端兑换登录、建立网盘服务 Cookie，并真实验证账号标识和根目录读取；通过后才保存并切换账号。
5. `authorized` 后读取 `/status` 与 `/files?dir=/`。刷新与服务重启使用已保存的私有凭证恢复。

会话状态：`waiting/scanned/authorizing/authorized/expired/failed/cancelled`。二维码约 5 分钟有效。会话按本站用户隔离，每人只允许一个进行中的扫码会话。失败或取消的新登录保留原有效账号。

`POST /auth/revoke`（`{confirmed:true}`）退出当前项目连接并删除本地登录凭证，不退出百度手机客户端。

Cookie、BDUSS、STOKEN、临时登录凭证只在服务端处理，不进入 API 响应、浏览器存储或命令行参数。私有记录在 `BAIDU_AUTH_DIR/user-{本站用户ID}/pcs`，目录 0700，文件 0600。稳定账号 ID 根据真实百度账号标识计算，账号切换不会混用文件缓存或分享记录。

旧官方 bdpan Skill 的 OAuth 凭证保留在原目录，不自动转为网页登录凭证。新接入需扫码；可以读取原 `/apps/bdpan` 目录，但当前根目录已经是 `/`。旧同步资料绑定的旧连接 ID 不会被猜测迁移，需要重新导入到已确认的账号。

## 接口清单

以下路径均相对于上述 API 前缀。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/capabilities` | 实际支持的操作、未支持项目和大小限制 |
| GET | `/status` | 真实账号与连接状态、可用容量信息 |
| GET | `/auth/config` | 授权方式与根目录说明，不含凭证 |
| POST | `/auth/start` | 创建真实扫码会话 |
| GET | `/auth/sessions/{id}` | 查询会话状态 |
| POST | `/auth/sessions/{id}/cancel` | 取消本用户扫码会话 |
| POST | `/auth/revoke` | 退出当前连接 |
| GET | `/files` | 目录列表、递归关键词搜索、分类与分页 |
| POST | `/upload` | multipart：多个 `files` 与绝对目录 `dir` |
| GET | `/download?path=...` | 下载普通文件，JWT 鉴权，不向浏览器暴露百度下载令牌 |
| POST | `/operations/{operation}` | 文件操作，详见下表 |
| GET | `/shares?page=1` | 百度账号真实分享记录，每页 100 项，`has_more` 表示可能还有下一页 |
| GET | `/shares/{share_id}` | 读取原有分享的原生链接和提取码；先查询当前账号列表，再核对归属 |
| POST | `/shares` | 创建原生百度分享，支持提取码和有效期 |
| POST | `/shares/{share_id}/cancel` | `{confirmed:true}`，取消原生分享 |

`PUT /auth/config` 和 `POST /auth/complete` 保留兼容性拒绝说明，不再接收旧 AppKey/授权码完成网页登录。

### 文件操作

| operation | 请求字段 |
|---|---|
| `create-folder` | `dir,name,confirmed:true` |
| `copy` / `move` | `paths,target_dir,confirmed:true` |
| `rename` | 单个 `paths,name,confirmed:true` |
| `delete` | `paths,confirmed:true`；根目录拒绝删除 |
| `file-meta` | `paths` 或已查询到的 `fsids` |
| `upload-text` | `dir,filename,content,confirmed:true` |

示例：

```json
{"dir":"/闲鱼资料","name":"课程","confirmed":true}
```

路径是网盘绝对路径。拒绝路径穿越、控制字符和根目录变更。文件 ID 为字符串，避免 JavaScript 大整数精度损失。提供 `paths` 与 `fsids` 时必须匹配当前真实文件，不会只靠过期缓存操作同名替代文件。上传、复制、移动和重命名拒绝覆盖现有同名内容。

`/files` 参数：`dir`、`keyword`、`page`、`page_size`、`category`。关键词搜索递归查询当前目录及子目录；分类显示当前目录。分页最多 100 项，目录分类及搜索结果上限 10,000 项，超过时要求缩小范围。

### 分享与 AI 发货

创建请求：

```json
{"paths":["/闲鱼资料/课程.zip"],"title":"课程资料","period":1,"password":"a123","confirmed":true}
```

`period` 为 `0/1/7/30` 天，0 表示永久。`password` 可省略，由项目生成 4 位字母数字提取码。`title` 是本项目显示与记录名称，不冒充百度提供的分享标题修改接口。

返回 `{share:{id,share_url,passcode,period,created_at,expires_at,status},saved}`。`share_url` 为真实 `https://pan.baidu.com/s/...` 链接；调用方确认 `success`、`saved` 和分享状态后再用于资料发布/卡券。云端已创建而本地保存失败时，返回有效链接及明确提示，不伪造“创建失败”让调用方盲目重试。

取消前会检查当前用户的同步资料、已发布版本及文字卡券引用。仍用于发货的链接必须先替换；数据库检查失败或资料任务运行中会阻止取消。

账号分享列表来自百度。百度部分历史记录不提供创建时间和提取码：`created_at:null`、`period:-1` 表示未知；需要时调用详情接口读取真实提取码。缺少私密分享提取码时标记 `unavailable`，页面不能直接复制不完整发货文本。`total` 是当前页数量，不是全账号总数。

统一同步资料已经调用相同百度底层，新百度副本路径为 `/闲鱼同步资料/{资源ID}/v{版本}`，保留资料版本、账号绑定、内容校验和现有卡券发布流程。

## 本地构建与依赖

源码和 License 位于 `backend-web/vendor/baidupcs-go`。本地工具链及缓存仅在当前项目 `data/local-dev/runtime`，不修改系统 Go、其他项目或全局账号配置。

```bash
sh backend-web/vendor/baidupcs-go/cmd/xianyu-bridge/build.sh --test
```

配置：`BAIDU_PCS_BIN` 可覆盖私有桥接位置，`BAIDU_AUTH_DIR` 指定私有授权目录；每批上传默认 1 GiB，普通文件下载默认 256 MiB，分别可通过 `BAIDU_MAX_UPLOAD_BYTES`、`BAIDU_MAX_DOWNLOAD_BYTES` 调整。

复用 BaiduPCS-Go（Apache-2.0）并新增 stdin JSON 薄桥接。上游默认跳过 TLS 校验的问题已最小修补，并保留本地 TLS 回归测试和 `UPSTREAM.json` 记录。扫码端点参考 [BaiduPCS-Go-HTTP](https://github.com/aki66938/BaiduPCS-Go-HTTP/blob/3d019e2aa71ea7208ad0866bcd60cd9904a24cba/internal/pcscommand/qrlogin.go)，加入用户隔离、正确网盘 Cookie 选择、HTTPS 跳转限制及真实身份验证。未新增 Python 或前端依赖。

## 当前边界

当前不支持分享转存、URL 上传、内容语义搜索和文件夹打包下载；能力接口和页面如实禁用。BaiduPCS-Go 依赖百度网页登录接口，平台变化或凭证失效时需要重新验证/扫码，不保证接口永久稳定。

本轮仅本地开发与验证，未部署。2026-10-10 已真实扫码读取原有根目录，并验证后端重启恢复、上传、复制、移动、重命名、删除测试副本、下载字节一致、递归搜索、1天原生分享、真实提取码和独立分享取消。171项后端回归、13项Go回归及TypeScript/Vite构建通过。真实验证记录见项目私有 `data/local-dev/baidu-pcs-verification.json`；单元测试的 provider fixtures 仅用于回归，不能代替实际账号验收。
