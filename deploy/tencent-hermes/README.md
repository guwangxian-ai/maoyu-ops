# 腾讯云共享服务器部署

目标：`https://43.136.91.22/xianyu-auto-reply/`，仅部署主系统。`promotion` 子系统和移动端不在本编排中。

## 当前开发与部署流程

自 2026-10-13 起，按用户约定使用“本地开发与必要构建 → 云端部署 → 云端验证”。本地不跑功能验收或测试套件；前端需要发布时在 `frontend` 执行 `PUBLIC_BASE_PATH=/xianyu-auto-reply/ npm run build`，其余命令沿用本文。此约定不构成后续任意部署的授权，每次部署仍须属于用户当前任务或已有明确授权。

1. **准备本次发布。** 核对 [部署记录](../../docs/tencent-hermes-deployment.json) 和现有服务器状态，仅打包本项目所需改动。排除本地凭证、`.env` 和私有运行数据，保留云端已有配置与授权卷。
2. **先备份，再切换。** 为本次发布建立独立备份目录，保存切换前的源码、站点和数据库备份，保留原镜像 ID 及对应回滚标签、本项目私有部署配置。涉及 Nginx include 时同时备份原文件；不输出其中的凭证。
3. **确认备份可用。** 必须确认各备份命令成功、归档和数据库备份可读取、回滚镜像确实存在，并记录完整备份路径与回滚办法。失败或未确认完整性时，不覆盖线上源码/站点、不切换容器；先解决备份问题。
4. **只切换本项目。** 使用下文专用 Compose 和已有 Nginx include，按变更范围更新必要的容器与站点。其他项目、持久化卷和全局环境保持原状。
5. **云端验证并记录。** 检查容器健康、页面/静态资源、API 和当前任务的核心流程；Skills/MCP 验证使用服务器实际提供的说明、技能包和接口，见 [AI 接入中心](../../docs/ai-access-center.md)。默认只读，真实商品发布、修改/下架/删除、分享、买家消息及发货须在用户授权范围内。测试套件如需执行，使用服务器隔离环境及独立测试库，不在生产业务库中跑测试。
6. **验收或回退。** 确认共享服务器其他项目入口正常，将版本、备份与回滚路径、真实验证结果及未验证项写入部署记录。验证失败按本次备份回退；需要用户完成登录/验证码时明确保留未完成项，不能把构建通过当成功能验证通过。

历史文档中的本地验收结果保留为当时记录，不作为今后继续运行本地测试的要求。项目协作规则见根目录 [AGENTS.md](../../AGENTS.md)。

## 隔离边界

- Compose 项目名为 `xianyu-auto-reply`，不声明固定容器名，不复用其他项目的网络或数据卷。
- MySQL、Redis、WebSocket 和 Scheduler 不发布宿主机端口。Backend-Web 仅绑定 `127.0.0.1:18089`。
- 每个容器有独立内存上限，并提高本项目容器的 OOM 终止优先级，减少资源紧张时影响已有进程的风险。
- 服务器的 Docker 默认继承 WireGuard DNS，该地址在容器内不可达。本编排仅给本项目容器指定服务器已有的腾讯云 DNS；镜像构建使用宿主机网络，不更改 Docker 全局配置。
- WebSocket 与 Scheduler 的部署专用 Dockerfile 以本项目先构建的 Backend-Web 镜像为基础，复用已安装的 Chromium 和系统库。
- 服务器 WireGuard 的策略路由会把宿主机到 Docker 网桥的流量导向 VPN。本编排固定独占网段 `172.18.0.0/24`，`xianyu-docker-route.service` 仅让该网段优先使用本机 Docker 路由，使 Nginx 能连接到 `127.0.0.1:18089`。
- 前端在本地以 `PUBLIC_BASE_PATH=/xianyu-auto-reply/ npm run build` 构建，静态文件由宿主机 Nginx 读取。
- 夸克授权、百度授权和同步资料分别保存在本项目的 `quark_auth`、`baidu_auth`、`cloud_asset_data` 私有卷，重建容器不会丢失。部署包不携带本地网盘凭证，云端账号通过云端页面授权。
- AI 接入中心位于 `/xianyu-auto-reply/ai-access`，公开说明为 `/xianyu-auto-reply/ai`，MCP 为 `/xianyu-auto-reply/mcp`。私有暂存使用 `mcp_files` 卷；技能包只包含固定白名单内的公开源码和本实例配置模板，密钥在云端单独签发。
- 百度网盘桥接由本地已安装的私有 Go 工具链交叉编译，作为只读文件挂载到 Backend-Web；不在服务器或其他项目安装 Go，不把本地 macOS 二进制直接用于 Linux。
- 宿主机 Nginx 的 AI 路由限定于 `/xianyu-auto-reply/`，保留前端已有 `/api/v1/`、`/static/` 路径和其他项目 location。
- 不执行仓库根目录的 `deploy.sh`、`update.sh` 或默认 Compose；它们使用固定容器名和宿主机端口，不适合这台共享服务器。

## 服务器布局

```text
/srv/xianyu-auto-reply/
├── source/                    # 本项目源码和 deploy/tencent-hermes/.env
└── site/xianyu-auto-reply/   # 前端 dist 内容

/etc/nginx/xianyu-auto-reply-location.conf
/etc/systemd/system/xianyu-docker-route.service
```

`nginx-location.conf` 需要作为 `include` 加到现有 IP HTTPS server 内；修改前备份现有配置，执行 `nginx -t` 成功后才 reload。HTTP 入口沿用现有 308 HTTPS 跳转。

固定的 Docker 网段需要优先于服务器的 WireGuard 默认路由。在启动应用前安装本项目的独立 unit：

```bash
sudo install -m 644 deploy/tencent-hermes/xianyu-docker-route.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now xianyu-docker-route.service
```

## 启动与检查

Ubuntu 24.04 的 `docker.io` 和 `docker-compose-v2` 可从服务器已有的 Ubuntu 软件源安装。服务器原先没有 Docker，安装属于系统级变更，须先取得用户确认。

在 `/srv/xianyu-auto-reply/source` 放置代码后，复制 `.env.example` 为 `.env` 并用独立随机密码填写，权限设为 `600`。源码中的前端 `dist` 不需要进入镜像。

百度网盘运行前，先在开发机的项目根目录构建与服务器架构匹配的桥接程序；当前腾讯云为 `x86_64`，使用 `amd64`：

```bash
sh deploy/tencent-hermes/build-baidu-bridge.sh amd64
```

将生成的 `deploy/tencent-hermes/bin/xianyu-baidu-pcs-bridge` 一并放到服务器相同路径，保持可执行权限。该脚本复用 `data/local-dev/runtime/go` 和已有模块缓存，遵守 vendor 的 `go.mod`、`go.sum`，不下载全局工具链。源码及 Apache-2.0 许可保留在 `backend-web/vendor/baidupcs-go`。Compose 在桥接文件缺失时拒绝启动。

`QUARK_FAST_READ` 默认为 `true`；可在本项目部署 `.env` 中设为 `false` 并重建 Backend-Web，恢复官方 CLI 的原始诊断等待。

```bash
cd /srv/xianyu-auto-reply/source
sudo docker compose -f deploy/tencent-hermes/compose.yaml --env-file deploy/tencent-hermes/.env config --quiet
sudo docker compose -f deploy/tencent-hermes/compose.yaml --env-file deploy/tencent-hermes/.env build backend-web
sudo docker compose -f deploy/tencent-hermes/compose.yaml --env-file deploy/tencent-hermes/.env build websocket scheduler
sudo docker compose -f deploy/tencent-hermes/compose.yaml --env-file deploy/tencent-hermes/.env up -d
sudo docker compose -f deploy/tencent-hermes/compose.yaml --env-file deploy/tencent-hermes/.env ps
curl -fsS http://127.0.0.1:18089/health
```

首次启动会创建 `admin/admin123`。必须在开放 Nginx 路径之前，用本目录的 `rotate_admin.py` 将密码改为随机强密码。脚本从权限为 `600` 的文件读取密码，不输出密码；不要在公开入口保留默认密码。
脚本同时通过已有的 `show_default_login_info` 设置关闭登录页上的旧演示账号提示。
轮换成功后删除宿主机和容器中的明文密码文件，把新密码只交付给管理员。

上线后检查主站的页面、静态资源、未登录 API 响应和三个已有项目的原路径，并监测内存、磁盘和容器重启次数。

## 回退

普通发布失败时，使用本次备份恢复原源码、站点、私有配置及原镜像，维持已有项目入口；回滚脚本和备份位置以 [部署记录](../../docs/tencent-hermes-deployment.json) 为准，先核对其对应发布版本和操作范围。数据库发生不兼容变更且确需恢复备份时，先核对备份后的新增业务数据，避免不可恢复覆盖。恢复后重新检查本项目健康与其他项目入口。

以下步骤用于明确要撤销本项目部署的场景，不代替普通版本回退：

先从现有 Nginx HTTPS server 移除本项目的 `include`，执行 `nginx -t` 后 reload；这只撤销本项目入口。再执行以下命令停止本项目容器：

```bash
cd /srv/xianyu-auto-reply/source
sudo docker compose -f deploy/tencent-hermes/compose.yaml --env-file deploy/tencent-hermes/.env down
```

不要添加 `-v`，以保留 MySQL、Redis、上传文件和备份数据卷。其他项目的服务和数据不在本 Compose 项目内。
如果彻底撤销部署，还需 `sudo systemctl disable --now xianyu-docker-route.service` 并删除对应的 unit 文件；普通应用停止或重启无需更改路由规则。
