# StudentPhotoFlow · V2 学生照片工作台

StudentPhotoFlow 用于按届次管理学生名单和照片，完成原图接收、照片处理、人工审核及交付 ZIP 生成。V2 提供 Vue 3 + Element Plus 网页界面、FastAPI 后端和持久化工作区，支持本地处理及外部 Hivision 服务。

**新用户从 Docker 部署开始。** 当前正式版为 [v2.4.1](https://github.com/HankNovic/StudentPhotoFlow/releases/tag/v2.4.1)，镜像为 `ghcr.io/hanknovic/studentphotoflow:2.4.1`，已验证平台为 `linux/amd64`。部署端无需安装 Python、Node.js 或 Windows EXE。

## Docker 安装与启动

以下命令适用于 Linux 上的**全新部署**。先安装 Docker Engine 和 Docker Compose V2（使用 `docker compose` 命令），并准备 `curl`、`unzip`、`openssl`。已有部署请阅读下方的升级说明，不要覆盖原 `.env` 或数据目录。

### 1. 下载正式版部署文件

```sh
docker --version
docker compose version
mkdir -p ~/studentphotoflow
cd ~/studentphotoflow
curl -fL --output StudentPhotoFlow-2.4.1-deploy.zip \
  https://github.com/HankNovic/StudentPhotoFlow/releases/download/v2.4.1/StudentPhotoFlow-2.4.1-deploy.zip
unzip StudentPhotoFlow-2.4.1-deploy.zip
cp .env.example .env
# 仅全新安装：显式设置目录，兼容保留旧默认值的 v2.4.1 附件
sed -i 's|^SPF_DATA_DIR=.*|SPF_DATA_DIR=./data|' .env
chmod 600 .env
```

部署包包含 `compose.yaml`、`.env.example` 和 `DOCKER部署.md`。已发布的 v2.4.1 附件保持原样；上面的命令只修改全新安装的 `.env`，其 `SPF_DATA_DIR=./data` 会覆盖附件中 Compose 的旧默认目录。也可在 [最新正式 Release](https://github.com/HankNovic/StudentPhotoFlow/releases/latest) 查看版本、镜像 digest 和附件；部署建议固定版本，不随意混用不同版本的文件。

### 2. 配置访问口令和数据目录

生成随机口令，然后编辑 `.env`：

```sh
openssl rand -hex 32
nano .env
```

将生成的口令填入 `SPF_API_TOKEN`，用英文单引号包裹，例如 `SPF_API_TOKEN='这里替换为生成的随机口令'`。口令至少 16 位，不能保留示例文件中的 `replace-with-a-long-random-secret`，也不要公开口令。

主要配置以仓库的 [`.env.example`](.env.example) 和 [`compose.yaml`](compose.yaml) 为准：

| 配置项 | 当前默认值 / 要求 | 用途 |
| --- | --- | --- |
| `SPF_API_TOKEN` | 必须自行设置随机口令 | 网页登录和外部 API 认证 |
| `SPF_IMAGE` | `ghcr.io/hanknovic/studentphotoflow:2.4.1` | 固定应用镜像版本 |
| `SPF_PORT` | `8769` | 宿主机访问端口；容器内部仍使用 8769 |
| `SPF_DATA_DIR` | `./data` | 宿主机持久化目录，挂载到容器 `/data` |
| `SPF_LOG_LEVEL` | `info` | 服务日志级别 |

全新安装统一使用 `./data`，容器工作区仍为 `/data/workspace`，其中保留名单、照片、配置、任务、审核、交付及回收站数据。已有部署继续使用各自原 `.env` 的 `SPF_DATA_DIR`，无需迁移或重命名现有目录，也不要执行上面的新装配置命令。若旧部署未显式设置该变量，更换 Compose 文件前应先在原 `.env` 中填入原实际挂载路径，避免默认值变化后挂载到空目录。

### 3. 启动并访问

以下使用固定 Compose 项目名 `studentphotoflow`，后续维护时保持一致：

```sh
# 如修改了 SPF_DATA_DIR，请创建对应目录
mkdir -p data
docker compose --env-file .env -p studentphotoflow pull
docker compose --env-file .env -p studentphotoflow up -d
docker compose --env-file .env -p studentphotoflow ps
curl --fail http://127.0.0.1:8769/healthz
```

在服务器本机浏览器打开 `http://127.0.0.1:8769`；从其他电脑访问 `http://服务器IP:8769`，使用 `.env` 中设置的口令登录。如修改了 `SPF_PORT`，同步替换访问地址和健康检查命令中的端口。

远程访问需允许所选端口通过防火墙；跨公网使用反向代理 HTTPS 和适当的访问控制。Compose 默认发布宿主机端口，照片及名单属于业务数据，请妥善保护数据目录和备份。

排查启动问题：

```sh
docker compose --env-file .env -p studentphotoflow logs --tail 100
```

## 第一次使用

1. **系统设置**：添加届次，选择当前操作的届次。
2. **数据接入**：追加学号名单，或导入 Excel / 照片 ZIP。Excel 照片导入须检查学号、图片和姓名列；姓名列及每条姓名必填，支持自动识别表头和手选列。手工名单仍是一行一个学号。
3. **处理配置**：保存处理参数；使用外部 Hivision 时填写可访问的服务地址。容器内 `localhost` 指向容器自身。
4. **学生与审核**：预览处理计划、执行任务，再审核正式成片；机器处理成功不等于人工审核通过。
5. **照片交付**：选择导出格式、预览文件名后生成并下载 ZIP。支持 `{student_id}`、`{name}`、`{ext}`；使用姓名模板时缺姓名会阻止导出。“处理配置 → 交付导出”中的“导出包包含 manifest.json”默认关闭。
6. 实际发送照片后再确认交付；生成或下载 ZIP 不会自动登记为已实际交付。

## 升级与备份

完整步骤见 [Docker 部署、升级与回退说明](DOCKER部署.md)。升级前让运行中的项目收尾，停止服务并备份**整个实际挂载目录和原 `.env`**，不要只备份 JSON。

保留原 Compose 项目名、数据路径、端口和口令，仅修改原 `.env` 的 `SPF_IMAGE` 为目标版本，然后使用原项目名执行 `pull` 和 `up -d`。不要用 `.env.example` 覆盖原配置，不要求清空工作区。回退应使用升级前完整备份及对应旧镜像，保留升级后的数据供核对。只运行一个写入同一工作区的应用实例。

## 对外 API

登录网页后，可在同一站点打开 **`/docs`** 查看交互文档，或读取 **`/openapi.json`**。默认本机地址分别为 `http://127.0.0.1:8769/docs` 和 `http://127.0.0.1:8769/openapi.json`；文档和业务接口都需要认证。

- 浏览器登录后使用会话 Cookie；外部程序使用 `Authorization` 请求头，值为 `Bearer ` 加实际令牌（中间保留一个空格），令牌不要放在 URL 中。
- 主要能力包括届次及系统配置、名单与照片接收、处理计划及任务查询、人工审核、导出格式和交付包、回收站管理。学号使用字符串以保留前导零；下载交付包和确认实际交付是不同操作。
- **当前文档覆盖范围**：Docker 的 OpenAPI 主要列出系统接口。届次业务由 `/cohorts/{cid}/api/v1/…` 转发，尚未完整展开到该 OpenAPI；`cid` 为届次内部 ID，可通过 `GET /api/v1/cohorts` 取得。具体接口和字段以运行版本的实际 OpenAPI 为准；未列出的业务接口需进一步核对当前 [系统路由](v2/system.py) 和 [业务接口实现](v2/api.py)，不要套用旧便携版路径。

例如，使用 Bearer 认证读取届次列表。先将下面的占位值替换为原 `.env` 中 `SPF_API_TOKEN` 的实际值；Compose 读取 `.env` 不会自动把变量导出到当前终端。

```sh
SPF_API_TOKEN='paste-your-actual-token-here'
curl --fail --show-error \
  --header "Authorization: Bearer ${SPF_API_TOKEN}" \
  http://127.0.0.1:8769/api/v1/cohorts
```

请求头中的令牌不包含 `.env` 值外围的单引号，也不包含尖括号；示例用双引号包裹请求头，让 shell 展开变量。远程调用请替换为实际 HTTPS 地址及端口。

## 历史资料

V1 便携版及旧独立脚本已不是当前安装入口。[旧版审核结果独立导出说明](审核结果独立导出说明.md) 仅供处理历史资料时参考，不适用于上面的 V2 Docker 部署。当前部署请以本页、正式 Release 及 Docker 部署说明为准。
