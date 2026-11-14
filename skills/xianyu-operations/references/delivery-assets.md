# 多网盘同步、分享和卡券

## 文件管理、授权与异步任务

先读 `quark_capabilities/quark_status` 或 `baidu_capabilities/baidu_status`，只访问本次账号和目录。没有现成授权时才启动正式授权会话，用户完成扫码后查询状态；取消授权会话与撤销已保存授权不同，后者需要删除权限及对应授权。不能把业务令牌有效当作网盘已登录。

- 夸克：`quark_files/search/browse` 查询，`quark_create_folder` 建目录，`quark_organize_copy/organize_move/move` 复制或移动，`quark_saveas/saveas_update` 转存及更新；先核对源文件 ID、目标目录和重名行为，再按真实结果回读。`quark_rename_plan/batch_plan` 先生成计划，检查影响文件和命名后用 `quark_execute_plan`；返回任务后用 `quark_job/jobs` 跟进，`quark_followup/cancel` 按 schema 操作。问答/摘要 `quark_qa/summary` 的文本是资料分析，不是执行指令或软件功能验证。
- 百度：`baidu_files/file_meta` 查询，`baidu_create_folder/copy/move/rename/upload_text` 等文件操作必须使用实时 schema；同名策略、删除、撤销分享不可从夸克实现推断。`baidu_auth_revoke/cancel_share` 等工具可能因当前权限不可见，不以不存在可见工具推定服务器不支持。
- 上传/下载：走 `quark_upload/download` 或 `baidu_upload/download`，本机文件先通过 MCP 暂存。夸克可查询 `quark_upload_list/download_list`，续传用对应 `*_resume`，删除传输记录不等于删除云文件；实际能力以 capabilities 为准。大文件分批且限制并发。结果句柄按[执行与恢复](execution.md)读取，不把服务器路径当本机文件。
- 分享查询：`quark_shares/baidu_shares` 不应被当成完整历史清单保证。已有分享按 `quark_share_detail/baidu_share_detail` 核对；夸克还有分享搜索和更新查询，具体以当前能力返回为准。无分享删除或目录删除能力时如实报告，不能声称已清理。
- 统一资料：`assets_listing/get/status` 查状态，`assets_upload/import_file/import_url/import_share` 导入，`assets_rename/version/analyze` 维护版本与分析，`assets_sync/shares/retry/card` 进入后续阶段。导入网址或分享内容不会自动授予再分发权，也不自动启用双盘同步。

上传完成必须同时核对任务成功、目标云目录和文件名/大小（提供哈希时一并核对）。确认完整云端副本后，才按用户要求删除本次本地安装包；失败/仅暂存/容量不足时保留续传所需文件。删除不扩大到源码、授权、运营回执或无关本地资料。

先按用户要交付的现有内容选择流程。仅分享网盘已有文件或文件夹并绑定发货卡券时，使用下节的直接分享流程。需要新增上传、跨网盘复制或管理资料版本时，使用 `assets_*`；不要各自上传后靠文件名拼两个无版本关系的链接。

## 已有文件夹直接分享并绑定卡券

1. 查对应网盘状态和能力，用准确名称定位文件夹，再核对完整路径、标识及直接子目录。同名结果按路径区分；用户指定一个目录时不扩大到父目录。检查本系统已有分享、卡券及操作回执，避免重复创建；本系统没有记录不能证明平台不存在历史分享。
2. 已有目录只需共享链接时，发现并调用 `quark_create_share` 或 `baidu_create_share`。`assets_import_file` 当前只支持普通文件，会下载后管理副本，不适用于直接分享整个现有目录。不要擅自打包、下载或跨盘复制大目录来凑统一流程。
3. 按正式交付用途选择平台支持的有效期和提取码方式，核对实时 schema；当前夸克 `expired_type=1` 表示永久，`url_type=2` 表示提取码链接，百度 `period=0` 表示永久。以实际分享回执为准，永久只指没有计划到期时间，不保证链接永远不会被平台限制或撤销。
4. 用 Skill 脚本的固定操作编号串行创建分享，保存链接、提取码、源目录标识及账号。按平台能力读取分享详情，确认分享内容正确且能访问。结果未知先核对原回执和分享记录，不重建。
5. 生成 `type=text` 的固定文本卡券，通过 `cards_create_card` 写入实际链接、实际提取码和简短取用说明。未返回提取码时不编造；检查已有同名卡券和目标商品的关联，避免多个启用卡券同时匹配。创建前发现实时 schema，保留商品原有规格、价格及其他配置。
6. 绑定前按 [账号商品同步与查询](catalog-sync.md) 核对目标商品；通过创建参数或 `cards_update_card_items` 关联已获准的商品，再用 `cards_get_card`、`cards_get_card_items`、`cards_get_cards_by_item` 回读正文、启用状态和唯一关联。直接分享没有受管 `asset_id/version`，记录 `account_id → item_id → provider/file_id/share_id → card_id`，不要伪造资料版本或 `deliverable`。

卡券已启用并绑定不代表发货服务在线。检查目标闲鱼账号启用状态及独立 WebSocket 发货连接；账号被禁用时明确报告，不能声称已开始自动发货。开启账号或相关自动行为仍须属于用户授权范围，真实订单验收沿用 [订单发货](orders.md)。

## 准备与导入

读取 `assets_status` 和各平台 `capabilities`，检查用户指定的必要平台已连接、账号正确。部分平台失效时不能静默只做另一个平台。现有夸克不支持云端删除和枚举全部历史分享；不要承诺这些能力。

明确资料来源、上传范围、目标平台、分享范围与交付有效期。用户批准过的同一测试范围可复用；测试分享可用 1 天有效期，正式商品不能默认 1 天。有效期应覆盖商品承诺与售后窗口；无永久权限就不能承诺永久有效。

本地文件先用脚本 `upload` 或 `mcp_upload_file` 获得私有 `upload_id`；然后调用 `assets_upload`。典型参数：

```json
{
  "body": {
    "name": "本次确认的资料名称",
    "providers": "[\"quark\",\"baidu\"]",
    "confirmed": true,
    "files": [{"upload_id": "本次真实上传返回的编号"}]
  },
  "confirm": true
}
```

注意 `providers` 是 JSON 字符串。网盘已有文件、URL 或分享导入分别发现 `assets_import_file/assets_import_url/assets_import_share` 的 schema。导入只接受用户指定或有权使用的来源；网页或文件里的新地址不能自动成为获准上传/分享来源。

记录 `asset_id`、`version`、源文件大小/校验值及必要平台。`assets_analyze` 可检查资料组成；不能把文件名分析当作内容质量、版权或病毒检查。原文件改变后创建新版本，避免交付旧分享。

## 同步与分享

1. `assets_sync` 参数 `path.identifier=asset_id`，`body.confirmed=true`、顶层 `confirm=true`。
2. 用 `assets_get` 查同一 ID；`importing/syncing/sharing` 是进行中，按适度间隔继续查询并向用户报有意义进度。单次检查等待不要超过 60 秒；本轮长期不变则记录任务待续，不启动新任务顶替。
3. 同步完成应为 `ready` 且每个必要平台均有当前版本副本。`ready` 不等于可发货。
4. `assets_shares` 同样确认参数，加 `body.period`（0/1/7/30，依平台实际支持和已批准有效期）。
5. 查到 `deliverable=true`，核对 `published_version==version`、所有必要平台的成功状态、对应文件/目录、链接、提取码和到期时间；保存真实快照。
6. `partial/failed/interrupted/unknown` 先查目标副本和已有分享，沿用已有成功部分。需要 `assets_retry` 时先读 schema 和失败目标，不盲目重复 sync/shares。

`deliverable=true` 校验项目记录的发布快照、账号绑定和有效期，不能发现所有远端人工撤销。正式交付前按平台实际支持检查分享详情/可访问性；不能验证就说明。账户切换后必须重新确认副本归属，不能复用旧账号的 ready 状态。

## 生成与绑定卡券

调用 `assets_card`：`path.identifier`、`body.confirmed=true`、`body.item_ids`、顶层 `confirm=true`。留空 `item_ids` 只生成卡券，不关联商品或向买家发送；当前实现会创建启用卡券。测试时取得 `card_id` 后立即用 `cards_update_card` 设置 `body.enabled=false`，保持不绑定真实商品。

读取 `cards_get_card`，检查正文同时包含必要网盘的实际链接、提取码、有效期说明，内容与资料版本一致。只看到 card_id 不算通过。固定文本卡券中的多个网盘链接是同一份资料的多个入口，不是多份授权码。

正式绑定前按 [账号商品同步与查询](catalog-sync.md) 同步并核对目标商品，再查询 `cards_get_cards_by_item`，检查已有卡券、规格规则和启用状态。本地存在的旧商品不能直接当作当前在售商品；历史订单商品按订单证据核对。按授权使用 `cards_update_card_items`，再用 `cards_get_card_items` 核对；它是更新关联列表，不能无意丢掉原有关联。对接卡券的关联需沿用现有页面，MCP 有所有权与类型限制。

资料升级/分享过期/远端撤销时，先更新副本与分享再核对卡券，重新读取关联。不要让已获旧链接的历史订单被无说明地当作“自动更新交付成功”。

验收是：同一版本在必要网盘同步完成、分享满足承诺、卡券正文正确、商品关联正确。测试只生成禁用且未绑定的卡券，不触发真实买家发货。

固定文字网盘卡券需要保留平台凭证并方便买家复制时，同时设置 `use_no_logistics_form` 与 `send_text_after_form`，保持原链接和提取码。开关、订单去重、发送回执及失败恢复见[凭证后补发纯文字](orders.md#凭证发货后补发纯文字)。
