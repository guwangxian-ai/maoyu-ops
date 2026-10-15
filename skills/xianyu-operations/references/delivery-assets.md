# 多网盘同步、分享和卡券

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
