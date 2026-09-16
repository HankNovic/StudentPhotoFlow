# StudentPhotoFlow 2.4.0-rc.5 · Linux Docker 部署

本候选版继续使用 Vue 3 + Element Plus、Notion 主题和单进程 Python 服务。本次不更新 stable/latest。镜像不依赖 Windows EXE 或宿主机开发环境。

## 从 rc.4 保留数据升级

rc.5 继续使用 rc.4 的 schema 3 届次目录。**保留现有 SPF_DATA_DIR、SPF_WORKSPACE 和 Compose 项目名**，不得改成空目录。学生、照片、配置、届次、回收站到期时间、审核和交付记录保留。每个子工作区首次启动时追加 task_semantics=2，并先写 backups/before-task-semantics-2.json；使用原子替换，读取或写入失败会报错，不重置数据。此索引备份不能代替整个数据目录备份。

更新前在任务页点击“暂停”，等待“已暂停”（保留继续资格），或等待自然完成。“安全结束”表示永久结束该任务，不可再继续。停止容器后备份整个原挂载目录。更新 .env 中 SPF_IMAGE=ghcr.io/hanknovic/studentphotoflow:2.4.0-rc.5，其余数据路径和口令保持原值。

```sh
# 在原部署目录执行；spf-rc4 替换为原有 Compose 项目名
# 先在页面暂停并等待当前学生保存完成
docker compose --env-file .env -p spf-rc4 images
docker compose --env-file .env -p spf-rc4 stop
# data-rc4 替换为 .env 的实际 SPF_DATA_DIR；不要备份其他空目录
tar -czf "data-before-rc5-$(date +%Y%m%d-%H%M%S).tar.gz" data-rc4
# 编辑 .env，仅更新 SPF_IMAGE；备份 .env 并私密保存
nano .env
docker compose --env-file .env -p spf-rc4 pull
docker compose --env-file .env -p spf-rc4 up -d
docker compose --env-file .env -p spf-rc4 ps
curl --fail http://127.0.0.1:8769/healthz
```

首次 rc.4→rc.5 需手动刷新已打开的旧页面：rc.4 没有构建标识轮询代码，无法事后给已加载的页面添加提醒。加载 rc.5 后，后续部署会提示“服务已更新，请刷新页面”；未保存输入先确认，刷新不终止后台任务。GitHub 有新版与当前网页过期是独立判断。

重建时中断的任务保留完成检查点，转为“系统中断，可核对后恢复”。在途结果不确定时单独列出；请核对本地与外部服务记录，再明确允许重试或不再执行，不会自动重复调用。用户手动结束属于终态，重启后仍不能继续。未执行数不计成功；需要处理剩余学生时另建计划。

rc.3 及更早数据仍不兼容：保留旧目录与旧镜像，在新目录部署，不自动覆盖原数据。此限制**不适用于 rc.4→rc.5**。

## 全新部署

下载本预发布的 compose.yaml、.env.example 到新的同一目录：

```sh
mkdir -p ~/studentphotoflow-rc4
cd ~/studentphotoflow-rc4
# 将发行版的 compose.yaml 与 .env.example 放入此处
cp .env.example .env
chmod 600 .env
# 本地可用 openssl rand -hex 32 生成随机口令，然后编辑配置
nano .env
mkdir -p data-rc4
 docker compose --env-file .env -p spf-rc4 pull
 docker compose --env-file .env -p spf-rc4 up -d
 docker compose --env-file .env -p spf-rc4 ps
curl --fail http://127.0.0.1:8769/healthz
```

访问 http://服务器:8769，在管理员登录页输入自己设置的口令。没有默认密码。生产远程访问请通过反向代理配置 HTTPS。会话 8 小时失效；退出登录仅结束当前会话，服务重启使所有会话失效。网页没有退出程序按钮，Docker 关闭服务接口不可用。

**口令使用英文单引号**，例如 `.env` 中 `SPF_API_TOKEN='你自己生成的至少16位随机口令'`，防止 `$` 被 Compose 插值。示例占位值会被后端拒绝。不要把真实口令放到 Git、聊天、命令参数或截图。

SPF_IMAGE 固定为 ghcr.io/hanknovic/studentphotoflow:2.4.0-rc.5；SPF_PORT=8769 为宿主机端口，容器内部始终 8769；SPF_DATA_DIR=./data-rc4；SPF_UPDATE_CHANNEL=candidate。如需固定 digest，将 SPF_IMAGE 改成发布说明中的 `ghcr.io/hanknovic/studentphotoflow@sha256:…`。

## 数据、回收站和并发

SPF_DATA_DIR 整目录挂载到 /data；工作区 /data/workspace。system.json 保存届次、保留期、系统审计；control 保存全局处理配置和 API 历史；cohorts/固定ID 保存每届学生、原图、成片、阶段图、版本、处理快照、任务、审核、交付包和回收站。改年份不改 ID、不移动数据。

只运行一个服务实例，禁止多进程、多副本共享工作区。活动任务期间禁止删除；需在任务页安全结束（用于移入回收站时，只暂停还不够），等当前照片结束再停止容器。不要手工删除照片文件。

回收站默认 30 天，可关闭到期清理。修改天数只影响以后的删除，不重置已有到期时间。关闭后暂停到期清理，重新启用会补清理原本到期记录。启动及每分钟检查；清理失败持久化状态并重试。已开始永久清理不能恢复。共享任务/交付包按完整关联学生范围回收，预览列出范围，整届清空须输入届次名称。同届同学号恢复冲突不覆盖。

## 一致备份、更新、恢复与回退

更新前等待任务完成或暂停到安全边界，记录版本、镜像 ID 和仓库 digest，停止服务后备份整个数据目录，不能只复制 JSON：

```sh
docker compose --env-file .env -p spf-rc4 images
docker image inspect ghcr.io/hanknovic/studentphotoflow:2.4.0-rc.5 --format '{{json .RepoDigests}}'
docker compose --env-file .env -p spf-rc4 stop
tar -czf "data-rc4-backup-$(date +%Y%m%d-%H%M%S).tar.gz" data-rc4
# 安全备份 .env，不与公开附件放在一起
# 阅读兼容说明，修改 .env 的 SPF_IMAGE 为目标固定版本或 digest
docker compose --env-file .env -p spf-rc4 pull
docker compose --env-file .env -p spf-rc4 up -d
docker compose --env-file .env -p spf-rc4 ps
docker compose --env-file .env -p spf-rc4 logs --tail 100
curl --fail http://127.0.0.1:8769/healthz
```

登录核对版本、届次、学生数量、照片、配置、审核和交付记录。健康检查不能代替业务检查。网页检查更新仅展示版本/说明；不拉镜像、不挂 Docker socket、不自动升级。固定标签不覆盖。

回退时停止新实例并保留新目录，使用旧镜像及与它匹配的备份。解压到独立恢复目录，修改 SPF_DATA_DIR 后启动。**换旧镜像不是数据回退**：rc.3 不支持 schema 3，rc.5 也不支持 rc.3 的旧目录；回退 rc.4 必须使用升级前整目录备份，因为 rc.4 的任务终态规则不同，不能将其恢复逻辑用于 rc.5 新记录。跨不兼容版本必须保留两套目录，不能自动迁移。

不要执行 down -v 或删除挂载目录。日志滚动保留 3×10MB。涉及访问配置的日志、备份须妥善保护。

## Hivision 与 GHCR

通过处理配置填写外部 Hivision 地址。同网络容器用服务名，其他服务器用可达 DNS/IP；宿主机可用 host.docker.internal（Linux 需 host-gateway）。容器内 localhost 只指本容器。本镜像支持本地快速处理、外部 Hivision；可选本地 AI 引擎以依赖检测为准。模拟验证不代表真实抠图效果。

公开镜像支持匿名 pull。私有包需要 docker login ghcr.io，使用 read:packages 凭据，不放入 compose。包管理：https://github.com/users/HankNovic/packages/container/package/studentphotoflow 。普通代码提交只构建，候选发布使用显式入口；本次发布推送本地验收的同一镜像，registry digest 见 Release。
