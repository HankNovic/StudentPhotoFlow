# StudentPhotoFlow Linux Docker 部署

复制 `.env.example` 为 `.env`，设置随机 `SPF_API_TOKEN`，然后执行 `docker compose pull && docker compose up -d`。浏览器访问 `http://服务器:8769`，在登录页输入令牌；没有默认密码。

数据持久化在宿主机 `SPF_DATA_DIR`（默认 `./data`）并挂载到容器 `/data`，工作区为 `/data/workspace`。不要让多个副本共享同一目录。更新前停止/中断任务并备份 `data`；记录旧镜像 digest。回退只更换镜像标签或 digest，不等于回退数据；有 schema 变更时按发布说明迁移，必要时从备份恢复到独立目录。

Windows 工作区迁移：备份原目录，将只读备份挂载到容器 `/migration:ro`，在“数据接入→迁移旧版数据”填写容器内路径，迁移目标必须是空 `/data/workspace`，不覆盖原目录。

Hivision：同一 Compose 网络填写服务名；宿主机服务用 `host.docker.internal`（Linux 配置 host-gateway）；其他服务器填写可达 DNS/IP。容器内 `localhost` 只指本容器。

GHCR 公开镜像可直接 pull；私有镜像先执行 `docker login ghcr.io`，使用含 `read:packages` 的凭据。stable 与固定版本应由同一已验证 digest 推送。网页检查更新只展示版本/说明并提示联系运维，网页不拉取镜像、不挂载 Docker socket、不升级容器。

常规更新：`docker compose pull && docker compose up -d`。首次部署和更新后检查 `docker compose ps`、`curl http://localhost:8769/healthz`、页面版本和 workspace 数据。
