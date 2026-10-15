# 夸克网盘接口

本模块复用 [夸克官方 Skill](https://github.com/quark-clouddrive/quarkclouddrive_offical) 的 1.0.22 CLI（Apache-2.0），不伪造云端结果。授权、文件权限、会员能力和配额以夸克实际返回为准。所有请求使用本站登录令牌 `Authorization: Bearer <token>`，数据按本站用户及当前夸克账号隔离。

本地入口：[夸克网盘页面](http://127.0.0.1:9000/cloud-drive/quark)。接口前缀为 `/api/v1/cloud-drive/quark`。API 列表与参数可查看本地后端 `/openapi.json`。JSON 响应遵循项目 `ApiResponse`，调用方必须检查 `success`；HTTP 200 不代表业务成功。项目会将参数校验异常转换为 HTTP 200 / `success:false,code:400`，认证错误通常为 HTTP 200 / `code:401`。

## 专用接口

| 方法 | 路径 | 能力 / 请求 |
| --- | --- | --- |
| GET | `/capabilities` | 当前版本、开放操作、限制及不支持的能力 |
| GET | `/status` | 持久化授权状态与真实账号连接检查 |
| POST | `/auth/start` | 生成官方授权地址 |
| POST | `/auth/sessions` | 创建扫码授权会话，返回 `session_id,status,authorization_url,expires_at,message` |
| GET | `/auth/sessions/{session_id}` | 查询本站当前用户拥有的授权会话；完成状态来自官方 CLI |
| POST | `/auth/sessions/{session_id}/cancel` | 停止对应授权等待进程；不撤销已经完成的官方授权 |
| POST | `/auth/complete` | `{ "code": "官方授权码", "session_id": "可选会话 ID" }`，由官方 CLI 换取并保存令牌 |
| POST | `/auth/revoke` | `{ "confirmed": true }`，返回官方解绑二维码地址；必须在夸克网盘独立 App 确认 |
| GET | `/files` | `parent_fid=0&keyword=&search_type=mix`，浏览 / 搜索，返回完整列表及文件元信息 |
| GET | `/shares` | 本系统创建的分享记录，含链接、提取码、有效期 |
| POST | `/shares` | `{ "fids": ["真实完整FID"], "title": "标题", "url_type": 2, "expired_type": 2 }` |
| POST | `/upload` | multipart：`files`（多个）、`relative_paths`（JSON 字符串数组）、可选 `parent_fid`、`record_id`（重新关联单个源文件并恢复上传） |
| POST | `/rename/plans` | `items` 数组，每项 `fid,parent_fid,old_name,new_name`，核对真实原名后生成待确认方案 |
| POST | `/batch-organize/plans` | `{ "proposal": { "version":4,"new_folders":[],"operations":[] } }`，原生 init/check 后生成待确认方案 |
| GET | `/jobs` | `offset=0&limit=50`，当前用户任务、进度、结果、待确认方案及下载清单 |
| GET | `/jobs/{id}` | 单个任务，刷新和重启后保留 |
| POST | `/jobs/{id}/execute` | `{ "confirmed": true }`，原子提交方案，重复点击不会重复执行 |
| POST | `/jobs/{id}/followup` | `{ "action":"revert", "confirmed":true }`，可用 action：`revert`、`rename-resume`、`resume`、`undo`、`undo-resume` |
| POST | `/jobs/{id}/cancel` | `{ "confirmed": true }`，中止本地等待 / 传输；已提交操作不会自动撤销 |
| GET | `/jobs/{id}/files/{file_id}` | 当前用户下载产物，带 attachment 头；使用本站令牌下载 |

分享类型：1 公开、2 带提取码；提取码由夸克生成。有效期类型：1 永久、2 一天、3 七天、4 三十天、5 六十天、6 一百天、7 一百八十天。

## 官方业务操作

统一入口 `POST /operations/{operation}`，JSON 字段在下表。`fids` 每次最多 100 项，使用接口返回的完整 FID。所有有副作用的操作必须带 `confirmed:true`。读取操作直接返回 `data` / `items`；其他操作返回持久化 `job`，客户端通过任务接口查询进度和结果。

| operation | 主要字段 | 作用 |
| --- | --- | --- |
| `browse` | `parent_fid,size` | 浏览目录，返回完整列表 |
| `search` | `keyword,search_type,parent_fid,size` | 搜索文件，类型为 mix/video/album/doc/audio/dir/package/other/app |
| `get-user-info` | 无 | 账号信息 |
| `create-folder` | `name,parent_fid` | 创建文件夹，可使用相对多级路径 |
| `move` | `fids,target_fid` | 移动文件 / 文件夹 |
| `download` | `fids` | 云端下载到私有服务端目录，完成后可通过附件接口保存到电脑 |
| `summary`、`qa` | `fids,query` | 所选文件 / 文件夹的内容总结和问答 |
| `organize` | `query` | 照片 / 视频智能整理，默认复制；大范围时可能要求再次确认 |
| `organize-copy`、`organize-move` | `task_id` | 仅使用 organize 返回的真实任务 ID，提交复制 / 移动选择；返回成功代表已提交 |
| `share-detail` | `url,passcode,page,size,parent_fid` | 分享详情及子目录浏览，支持手动翻页 |
| `share-search` | `url,passcode,keyword,page,size` | 分享内搜索 |
| `get-share-update-list` | `page,size` | 有更新的已转存分享列表，size 最大 50，逐条结果在 items |
| `get-share-update-files` | `url,page` | 新增文件，官方每页最多 5 项；不自动翻页 |
| `get-share-saved-dir` | `url` | 获取原转存目录 |
| `saveas` | `url,passcode,fids,target_fid,use_saved_directory` | 转存分享全部或所选文件；省略 target_fid 则使用官方默认位置 |
| `saveas-update` | `url` | 将全部新增文件转存到原目录，不允许指定 fids 或改目标 |
| `upload-list`、`download-list` | 可选 `state` | 官方传输持久化记录，逐条数据在 items |
| `upload-resume`、`download-resume` | `record_id` | 恢复原传输断点；上传需原暂存文件仍在，可用 multipart 重新关联源文件 |
| `upload-delete`、`download-delete` | `record_id` | 只删除本地传输任务记录，不删除云端文件 |

分享 URL 仅接受 `https://pan.quark.cn/s/...`，可带 `pwd`。只转存部分更新时传 `fids` 与 `use_saved_directory:true`，后台先查原目录，再普通转存；原目录不可用时省略目录参数，使用官方默认位置。再次转存全部更新前，应重新读取更新列表并由用户确认，不自动重复提交。

`parent_fid=0` 在浏览时代表根目录，在搜索时会省略官方 CLI 的目录参数以执行全盘搜索。指定真实目录 FID 时严格保留该范围；如果官方目录搜索失败，不会自动扩大范围或伪造结果。
只选择类型而未填写关键词时，读取真实目录快照，再按官方 category 元数据筛选。

## 方案、任务与恢复

任务的私有 `params._account_id` 固定绑定创建时的夸克账号；账号切换后不能查看、执行、恢复或撤回其他账号的任务。旧任务通过私有 `account-binding.json` 归属原账号，不重写历史记录。原账号的分享历史保持原文件位置，新账号分享写入独立账号目录，切回后恢复各自历史。

官方上传和下载断点记录使用当前夸克用户 ID 作为索引及记录键的一部分；本地已核对源码和现有索引归属。本站任务仍额外校验创建时的账号，防止换号后接续旧任务。

- 重命名最多 5,000 项，后端刷新原目录核对完整 FID 和原名。方案确认后使用固定 batch ID，接续原批次通过 `rename-resume`，撤回通过 `revert`。新名称按官方 UTF-16 长度上限校验。
- 批量整理使用官方 version=4 proposal。`new_folders` 包含 `plan_id,parent_fid,path`；`operations` 仅支持 `{op:"move",to,items:[{fid,parent_fid,origin_name,new_name?}]}`。`to` 可引用已有完整 FID 或本方案 `s_` 开头的 plan_id。其他官方 scope / target 字段可按原生协议传入。先 check，再以返回的 plan_digest apply；接续和 undo 使用原 task_dir / WAL，不向客户端开放宿主机路径。
- 正向整理接续 `resume`，撤回 `undo`，撤回中断后的接续 `undo-resume`。重命名和整理的撤回期由官方判断（通常 30 天）。本地记录需保留。
- 状态：`planned`、`queued`、`running`、`succeeded`、`partial`、`failed`、`needs_confirmation`、`interrupted`、`unknown`、`submitted`。未确认结果不会显示为已完成。
- 后端重启会将待执行和运行中任务标记 interrupted，不自动重放有副作用的操作。授权、分享历史、方案和任务记录均持久化。
- 下载断点与原输出目录绑定，恢复时沿用原有字节；没有可核验的目录记录时拒绝恢复并提示重新下载。只发布官方确认完成的文件，未完成的临时文件不作为下载产物提供。
- 同一夸克账号串行调用。读取请求最多排队等待 3 秒，避免同时检查状态或读取目录时立即误报忙碌；超过此时间仍返回任务忙碌提示。任务中心和本地分享历史仍可读取。

## 数据与限制

- 新表 `xy_cloud_drive_jobs` 由项目已有数据库初始化流程创建。授权与 CLI 状态保存在 `QUARK_AUTH_DIR/user-{本站用户ID}`，各用户独立；不要放进静态资源目录或提交到 Git。
- 上传暂存、下载文件和撤回记录也放在私有目录。目录权限 700、敏感文件权限 600。API 不返回夸克访问令牌、刷新令牌或服务端文件路径。
- 默认单次上传最多 1,000 文件、1 GB，可通过 `QUARK_MAX_UPLOAD_BYTES` 调整。当前版本不自动清理撤回 / 断点文件，避免破坏恢复；需保留数据卷并安排有意识的清理。
- 官方暂未提供云端文件删除与账号全部历史分享枚举，`capabilities.unsupported` 会明确返回。后台没有假删除接口。
- 官方 install/update/uninstall、logout 清理程序和 resolve-agent 诊断属于运行环境管理，不作为网盘业务接口暴露。版本更新通过审核固定 vendor 文件完成，不调用全局安装脚本。
- 没有新增运行时 Python / npm 依赖；复用 FastAPI、SQLAlchemy、MySQL、React、Axios 与 Node.js，以及官方 CLI 和 hash worker。

## 本地验证记录（2026-10-01 / 02）

用户已同意在「闲鱼接口测试」独立目录进行真实云端测试，测试文件不含业务内容或个人数据。所有生产业务实现均调用官方 CLI；单元测试中的替身只用于校验异常和边界。

- 已通过真实 API：授权持久化、账号读取、浏览、新建目录、上传、移动、重命名、重命名撤回、批量整理（新建多级目录 + 移动 + 改名）、整理撤回、下载及逐字节 SHA-256 校验、1 天有效的私密分享、分享详情、分享内搜索、传输记录读取、全盘搜索、服务重启后的授权 / 分享 / 任务 / 下载记录恢复。
- 已通过单元回归、TypeScript、前端构建及 React Hooks ESLint 检查。项目未提供常规 ESLint 配置，本次用已有 parser / hooks 插件显式指定规则；没有新增全局配置。
- 转存自身分享被官方拒绝（「用户禁止转存自己的分享」）。转存成功、全部 / 部分更新转存仍需其他账号的可转存测试链接验证。
- TXT 总结和问答被官方拒绝识别。Word 测试文档返回「分析你的网盘需要一定的时间，分析完成后可自由提问，请在24小时后重试」。接口已接入，但尚未验证 AI 的成功结果；不自动重试或伪造回复。智能相册整理和真实中断后的传输恢复也尚未进行云端成功测试。
- 官方指定目录搜索当前返回「文件找不到」；完整目录 FID 来自真实浏览和真实全盘搜索，两种来源均已核对。保留错误提示，全盘搜索已通过，不在后台自动扩大范围。
- 浏览器交互尚未验证：先前浏览器任务已被用户停止，本轮没有重启或替换该任务。验证证据来自真实接口调用与构建。
- 未部署到云服务器；本地前端 9000、后端 8089。

## 页面与扫码授权

- 页面复用已有闲鱼侧边栏，在夸克页内提供文件分类、列表/网格、排序、多选操作、分享转存、传输及任务中心。文件、容量和操作结果均来自真实接口。
- 夸克账号入口放在网盘内容区工具栏最右侧，容量与授权状态在下拉菜单中显示；分享、转存、传输和任务视图均保留账号入口。夸克页不再显示独立的大标题区，项目顶部的系统账号菜单仍由原系统管理。
- 官方授权页当前返回 `X-Frame-Options: SAMEORIGIN`，不使用跨域 iframe。当前页弹窗根据官方授权地址生成二维码，并保留官方授权页和手动授权码入口。
- `quark_login_bridge.cjs` 只把官方 CLI 的浏览器打开动作交给当前页面。未修改官方 CLI；它继续使用官方 `page_code` 查询、授权码交换与令牌保存流程。
- 官方 CLI 等待约 60 秒。状态为 `starting/waiting/authorized/expired/failed/cancelled`，其中 `expires_at` 使用 Unix 毫秒。只有原 CLI 成功并保存凭证，或校验到账号仍已授权，才返回 `authorized`；页面再次验证真实用户信息后加载文件。
- 取消或刷新会停止旧等待进程，旧会话不能替换新会话。关闭弹窗也会取消等待。会话仅保存在当前后端进程中，服务重启后需重新获取二维码，已经保存的账号凭证不会随之消失。当前项目后端为单进程；未来增加多个 worker 时应改用共享会话和进程调度。
- 退出和切换账号先生成官方解绑二维码，在夸克网盘独立 App 确认。页面检查到授权真正失效才退出；切换则继续发起新账号授权。存在排队/执行中任务时拒绝登录或解绑请求。不会把卸载专用 `logout` 当作普通退出接口。

2026-10-02 本地验证：前端构建、React Hooks 检查、33 项回归测试通过；真实账号的状态/文件/分享/任务读取通过；未绑定临时运行目录可生成真实官方扫码地址、拒绝跨用户访问并取消等待；已授权测试目录内的新建目录任务成功并可通过真实文件列表查询。手机实际扫码确认、完整退出及换账号仍需要账号持有人完成官方 App 确认后验收。

## 读取速度优化（2026-10-12，仅本地）

- 保留原官方 CLI、完整 JSONL 文件校验、账号互斥和正常进程退出。没有结果缓存，文件列表每次仍从夸克读取。
- `quark_read_bridge.cjs` 仅对 `get-user-info`、`browse`、`search` 生效。核对固定 CLI SHA-256、对应完整成功输出和精确调用位置后，把两处诊断收尾等待上限缩短为 100ms：配置读写探测的累计上报预算，以及需要时的传输诊断等待。其他计时器、业务请求、授权和写入流程保留原行为。
- 当前真实调用的主要等待来自配置探测：8 条诊断记录按每条 650ms 累计约 5.2 秒；另一个 500ms 诊断等待保留。缩短预算可能减少纯诊断探测或上报的完成数量，不影响已经完成的业务结果和授权保存；项目没有承诺遥测全部送达。
- 未知 CLI 版本、调用位置不符、失败或缺少文件清单、输出异常/过大、stdout 尚未写完时，继续原始等待。升级 vendor 时须重新审查对应调用位置并运行 `node --test backend-web/tests/quark_read_bridge_test.cjs`。
- `QUARK_FAST_READ=true` 默认启用；本地后端环境设置 `QUARK_FAST_READ=false` 并重启即可恢复原始诊断等待。仅正常退出码 0 的读取进程可作为成功结果返回。
- 前端按本站认证身份及完整参数合并相同的进行中 GET，结束后立即移除；刷新会重新请求。授权弹窗校验会话和请求版本，避免旧状态响应结束新二维码会话。
- 真实账号 HTTP 基线两轮：状态均值 5.683s、根目录均值 5.713s；优化后三轮：状态均值 0.973s、根目录均值 1.030s（减少 82.0%），根目录均返回 7 项。测试目录 2 项耗时 1.014s，全盘关键词搜索约 3.126s。
- 原始/优化模式下文件名称、大小、类型、分类、内容哈希和目录项目数一致。本次观察到官方完整 FID 每次读取会变化，项目不裁剪或持久缓存这些 FID。
- 大目录仍会按官方 `--all` 获取完整分页结果，其业务网络耗时不由本优化消除。没有新增依赖、付费服务或云服务器部署。
