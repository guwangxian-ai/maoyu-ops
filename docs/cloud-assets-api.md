# 多网盘同步资料与发货

本地页面：`http://127.0.0.1:9000/cloud-drive/assets`，菜单「网盘管理 → 同步资料」。原夸克、百度页面继续管理各自的文件和授权。

## 当前流程

上传本地文件 / 导入已有网盘文件 / 导入公开文件直链 / 导入官方网盘分享
→ 服务端私有暂存并计算 SHA256
→ 点击「同步网盘」，把同一版本的字节分别上传到所选账号
→ 点击「生成分享」，保存各平台真实链接、提取码与有效期
→ 全部指定平台成功后发布该版本
→ 生成或更新现有文本卡券，可关联本人商品，沿用项目自动发货。

分享链接导入先在来源盘真实转存，再下载普通文件。它不是把百度链接直接转存到夸克；跨平台同步依靠同一份暂存文件分别上传。

任务和资料记录保存在 MySQL，刷新可继续查看。每个网盘分别记录成功、失败或结果待确认。新版本未完整发布时，已有卡券继续使用上一发布版本；完整发布后自动更新关联卡券文本。

## API

前缀 `/api/v1/cloud-drive/assets`，使用现有本站 JWT。用户归属从 JWT 获取，客户端不能指定他人用户。响应采用项目 `ApiResponse`，错误也可能是 HTTP 200，必须检查 `success` 和 `code`。响应设置 `Cache-Control: no-store`。

| 方法 | 路径 | 参数或用途 |
| --- | --- | --- |
| GET | `/status` | 各网盘真实授权状态、上传限制与分享权限提示 |
| GET | `/` | `page`、`page_size`；当前用户资料分页 |
| GET | `/{id}` | 当前版本、副本状态、分析结果、已发布版本、`deliverable`、发货文本和卡券 ID |
| POST | `/upload` | multipart：`name`、`providers` JSON 数组、`confirmed=true`、多个 `files`；仅暂存 |
| POST | `/import` | JSON：`name`、`provider`、目标 `providers`、夸克 `file_ids` / 百度 `paths`、`confirmed:true` |
| POST | `/import-url` | JSON：`name`、`url`、`filename`、目标 `providers`、`confirmed:true`；公开 HTTP/HTTPS 文件直链 |
| POST | `/import-share` | JSON：`name`、来源 `provider`、`url`、可选 `password`、目标 `providers`、`confirmed:true` |
| PATCH | `/{id}` | `{name,confirmed:true}`；修改资料显示名称 |
| POST | `/{id}/versions` | multipart：多个 `files`、`confirmed=true`；建立下一版本 |
| POST | `/{id}/sync` | `{confirmed:true}`；异步核对并上传所选副本 |
| POST | `/{id}/shares` | `{confirmed:true,period:7}`；`period` 支持 0/1/7/30 天，0 为永久 |
| POST | `/{id}/retry` | `{confirmed:true}`；恢复当前版本最后一次失败、部分成功或中断任务 |
| POST | `/{id}/analyze` | `{confirmed:true}`；真实文件元数据与文本提取 |
| POST | `/{id}/card` | `{confirmed:true,item_ids:[]}`；创建或更新唯一关联文本卡券，商品 ID 可省略 |

示例 AI 工作流：`upload → sync → GET轮询 → shares → GET轮询 → card`。只有 `deliverable:true` 才表示已发布快照目前通过链接格式、平台完整性和记录有效期校验。`published_version` 可小于当前 `version`，此时发货仍使用旧版完整快照。

HTTP 接口可供未来 MCP 工具薄层调用；本轮未新增 MCP Server 或自动经营 Skill。

## 一致性与隔离

- 默认最多 100 个普通文件、每份合计 256 MiB。目录请先打包 ZIP；不会静默只导入分享中的前 100 项。
- 私有目录默认项目 `data/cloud-assets/user-{owner}/{id}/v{version}`，目录 700、文件 600；排除 Git、Docker 上下文和公开静态目录。`CLOUD_ASSET_DIR` 可覆盖，`CLOUD_ASSET_MAX_BYTES` 调整大小。
- 远端目标为夸克「闲鱼同步资料/{id}/v{version}」、百度 `/闲鱼同步资料/{id}/v{version}`。不同版本隔离，重试不覆盖同名冲突文件。
- 明确的远端 MD5 与本地 MD5 比较；未提供明确 MD5 时，真实下载副本并校验 SHA256。夸克不透明 `content_hash` 不冒充 MD5。
- 副本绑定授权账号，换号后拒绝继续操作旧副本。重新授权百度会生成新的连接身份。
- 同一资料最多一个活动任务；持久化具体任务 ID 与版本，旧结果不能写入新版本。意外异常会取消并等待同组任务，再释放该资料。
- 重启不自动重放外部写操作。结果不明的分享优先复用本项目已保存的同组文件分享记录；无记录则停止创建，避免生成重复链接。不能通过重新同步清除此限制。
- 来源转存未确认、部分成功或仅任务已提交时，不发布资料；失败来源导入不会自动重放转存。
- 直链下载限制协议、端口、大小和跳转次数，实际 DNS 连接解析拒绝内网目标。网页链接不当作文件下载。
- 发货重新验证发布快照；缺平台、链接格式错误、账号归属不符或已过期时，停止发货并在确认收货/无物流发货之前拦截。普通非关联卡券沿用原行为。

## 明确限制

- 这是项目资料副本的同步发布，不是全部网盘的双向镜像。现有各网盘文件管理接口保持独立；显示改名不自动改远端文件名，删除、移动和人工远端修改也不会跨平台传播。
- 百度使用 BaiduPCS-Go 管理当前扫码账号原有目录，新资料在 `/闲鱼同步资料`；分享返回百度原生链接，是否成功以真实接口结果为准。旧 Skill 连接需重新扫码确认账号，不猜测迁移绑定。
- 分享有效期校验使用记录；尚未实现发货前远端实时检查撤销、删文件、风控或链接可访问性。外部人工改动需在原网盘核验。
- Quark SDK 没有云端删除或枚举全部历史分享能力。无本项目记录的未知分享结果需在原网盘核验；本轮不提供伪造成功的恢复按钮。
- 文本分析是 TXT/MD/CSV/JSON/LOG/DOCX 的原文提取与元数据，不生成 AI 总结。其他格式只返回元数据。
- 私有暂存和旧版本保留供恢复，没有自动清理策略；需按实际资料量规划服务端磁盘。
- 本机当前代理将公网域名解析到 `198.18.0.0/15` 保留地址，公开直链的真实验收被安全检查拒绝。未放宽内网访问保护、未更改系统代理；正常公网 DNS 环境下需重新验收此入口。

## 复用来源

使用项目现有 SQLAlchemy/MySQL 任务、JWT、卡券与自动发货、React 页面组件和 aiohttp，未增加依赖。

- [夸克官方 Skill](https://github.com/quark-clouddrive/quarkclouddrive_offical)：项目已固定的 CLI 1.0.22，Apache-2.0；调用已有适配，未修改官方包。
- 百度：维护中的 `qjfoidnh/BaiduPCS-Go`（Apache-2.0）及私有 JSON 薄桥接，复用当前项目同一授权与文件接口；构建、源码版本及安全修补见 [百度接口说明](baidu-drive-api.md)。

测试使用临时文件及隔离 SQLite，只有外部调用在测试中替换；生产路由全部调用真实 CLI。SQLite 测试不验证 MySQL 行锁竞争。真实本地 HTTP 和浏览器验证的结果应与外部网盘上传、分享验收分别报告。
