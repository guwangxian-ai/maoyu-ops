# MaoYuOps（猫鱼运营）更名说明

2026-10-19，项目产品名调整为 **MaoYuOps（猫鱼运营）**，仓库使用 `guwangxian-ai/maoyu-ops`。项目定位为支持 MCP 与 Skills 的闲鱼 AI 运营工作台，涵盖商品、回复、网盘资料和卡券交付。

## 来源与贡献

本项目基于 [zhinianboke/xianyu-auto-reply](https://github.com/zhinianboke/xianyu-auto-reply) 二次开发。保留原有 Git 历史、Fork 来源、AGPL-3.0 许可证及第三方组件许可证；MaoYuOps 的新增能力与上游功能在 README 中分别说明。

原作者的社区、Windows 下载与更新服务属于上游项目。界面中的上游更新表示基础版本更新，不代表已经包含或验证了本项目的二开改动。更新本项目时应先审阅并合并上游变更，再验证和部署。

## 更名范围

- GitHub 仓库名、仓库简介、README 源码入口与文档标题。
- Web 标题、默认登录品牌、导航栏默认名称、关于页与桌面启动器展示名称。
- 前端包的名称字段；依赖版本与锁文件中的解析结果保持不变。
- MCP 公开接入说明中的产品名称；既有工具名、协议与 Skill 标识继续兼容。

已有实例在数据库保存的自定义系统名称继续生效，本次不会通过源码更新覆盖用户设置。服务器需在单独授权的部署中发布这些界面修改。

## 保留的运行标识

现有本地目录 `xianyu-auto-reply`、服务器目录 `/srv/xianyu-auto-reply`、Compose 项目与数据卷、数据库、Python 虚拟环境、授权目录继续沿用。保持 `xianyu-operations` Skill 和 `xianyu` MCP 连接名称，避免影响正在使用的开发环境和授权。

新克隆的目录可以使用 `maoyu-ops`。更名不会迁移或删除数据库、网盘凭证、卡券、运营回执、依赖或缓存。公开 Git 提交仅包含源码和项目文档，私有配置与运行数据继续通过 `.gitignore` 排除。

## 公网入口（2026-10-19）

按用户要求，公网地址调整为 `https://43.136.91.22/maoyu-ops/`，与仓库名一致。MCP 为 `/maoyu-ops/mcp`，公开接入说明为 `/maoyu-ops/ai`。旧页面地址保留 308 跳转，旧 MCP/API 地址继续代理至同一服务，方便已有客户端和历史交付链接继续使用。服务器数据目录、Compose 和数据卷名称不变；客户端仅更新 URL，不更换密钥或扩大权限。

浏览器 Storage 命名空间使用构建参数 `VITE_STORAGE_NAMESPACE=xianyu-auto-reply`，与原站点保持一致，保留已有登录态和偏好；新路径不会读取其他项目的 Storage。未配置该参数时仍按部署路径隔离。
