# StudentPhotoFlow 2.4.1-rc.3 · Linux Docker 候选部署

候选版以正式 v2.4.0 为业务基线，使用 Vue 3 + Element Plus、Notion 主题和单进程 Python。部署端无需编译，不需要 Windows EXE。默认固定镜像 ghcr.io/hanknovic/studentphotoflow:2.4.1-rc.3；本次不更新 stable/latest。固定版本不覆盖，实际 digest 见 Release。

## v2.4.1-rc.2 保留数据升级

本次 rc.2→rc.3 仅整理人工审核窗口交互，后端接口、导出命名和数据格式不变，无需迁移或清空。保留原有工作区。2.4.0 的兼容说明仍适用。

在原部署目录操作，保持原 Compose 项目名、SPF_DATA_DIR、SPF_WORKSPACE、端口和管理员口令。不要复制示例 .env 覆盖原 .env，不要新建空工作区代替现有数据。

1. 页面暂停任务，等待所有正在处理的项目完成并显示已暂停；或等待任务自然完成。安全结束属于不可继续的终态，仅在确实不再继续时使用。
2. 停止服务、备份整个实际挂载目录和 .env，记录旧镜像digest。
3. .env 中仅将 SPF_IMAGE 改为 ghcr.io/hanknovic/studentphotoflow:2.4.1-rc.3；SPF_UPDATE_CHANNEL 可改 rc（现有检查更新入口仍只查询正式 Release）。路径、口令不变。
4. 使用原项目名 pull/up，检查健康、版本、届次、学生、任务、照片和交付记录。

```sh
# 在原部署目录执行；两个值必须与原部署一致
SPF_PROJECT=spf-rc4
SPF_BACKUP_SOURCE=./data-rc4
# 先核对：docker compose ls；并查看原 .env 的 SPF_DATA_DIR
# 口令不要贴入聊天或日志
docker compose --env-file .env -p "$SPF_PROJECT" images
docker image inspect ghcr.io/hanknovic/studentphotoflow:2.4.1-rc.2 --format '{{json .RepoDigests}}'
docker compose --env-file .env -p "$SPF_PROJECT" stop
umask 077
SPF_BACKUP_DIR="backup-before-2.4.1-rc.3-$(date +%Y%m%d-%H%M%S)"
mkdir "$SPF_BACKUP_DIR"
cp .env "$SPF_BACKUP_DIR/environment.env"
tar -czf "$SPF_BACKUP_DIR/data.tar.gz" -C "$SPF_BACKUP_SOURCE" .
# 编辑原 .env，保留路径和口令，只改镜像为2.4.1-rc.3
nano .env
docker compose --env-file .env -p "$SPF_PROJECT" pull
docker compose --env-file .env -p "$SPF_PROJECT" up -d
docker compose --env-file .env -p "$SPF_PROJECT" ps
docker compose --env-file .env -p "$SPF_PROJECT" logs --tail 100
# 端口按原设置修改
curl --fail http://127.0.0.1:8769/healthz
```

2.4.0→2.4.1-rc.3 为兼容的增量字段升级，不重置数据；每个届次首次打开前保存 backups/before-task-timing-1.json 索引备份，完整照片仍需按上述命令备份整个目录。原子写入失败会报错，不清空或跳过原工作区。重建后需重新登录；已保存结果、配置、回收站保留期与任务进度保留。中断在途项目需人工核对后明确重试或跳过，不自动重复调用外部服务。前端提示服务已更新时先保存输入，再刷新。2.4.0-rc.3及更早仍不支持直接挂载，必须保留原目录另行处理；此限制不适用于2.4.0。

## 全新部署

下载本Release的部署ZIP，解压到新部署目录。原始文件名compose.yaml、.env.example及中文说明保留在ZIP中。单独下载时GitHub可能将.env.example规范化为default.env.example。

```sh
mkdir -p ~/studentphotoflow
cd ~/studentphotoflow
# 将部署ZIP解压到本目录后
cp .env.example .env
chmod 600 .env
# 生成随机口令，编辑.env，勿使用示例占位值
openssl rand -hex 32
nano .env
mkdir -p data-rc4
docker compose --env-file .env -p studentphotoflow pull
docker compose --env-file .env -p studentphotoflow up -d
curl --fail http://127.0.0.1:8769/healthz
```

管理员口令必须使用英文单引号，例如 SPF_API_TOKEN='你生成的至少16位随机口令'，防止 `$` 被Compose插值。无默认密码，示例值会被拒绝。访问 http://服务器:端口；远程使用反向代理HTTPS。SPF_PORT是宿主机端口，容器内部8769；SPF_DATA_DIR整目录映射/data，工作区/data/workspace。需要固定digest时将SPF_IMAGE设为Release中的ghcr.io/hanknovic/studentphotoflow@sha256:…。

## 备份、恢复和回退

只运行一个服务进程/副本，不让多个容器同时写同一数据目录。整个/data包含名单、原图、成片、配置、届次、回收站、版本、处理任务、审核和交付数据；不只备份JSON。自动清理仅处理回收站到期数据，已有到期时间不会因重启重置。

回退先停止新服务并保留新数据目录。将备份解压到另一个恢复目录，不覆盖当前目录，保留原始备份：

```sh
# 仍在原部署目录，使用前面的实际项目名和备份位置
docker compose --env-file .env -p "$SPF_PROJECT" stop
mkdir ./data-restored-rc2
tar -xzf "$SPF_BACKUP_DIR/data.tar.gz" -C ./data-restored-rc2
# 编辑.env：SPF_DATA_DIR=./data-restored-rc2；SPF_IMAGE改为旧2.4.1-rc.2版本或记录的digest
# 管理员口令保持原值
nano .env
docker compose --env-file .env -p "$SPF_PROJECT" pull
docker compose --env-file .env -p "$SPF_PROJECT" up -d
```

换回旧镜像不等于回退数据；从备份恢复会回到备份时间点，升级后新增业务需保留并另行核对。不得使用down -v或删除旧挂载目录。备份含业务数据及口令，应私密保存。

## Hivision、更新与日志

通过处理配置填写外部Hivision地址；同网络容器用服务名，其他服务器用可达DNS/IP，宿主机用host.docker.internal（Linux需host-gateway映射）。容器localhost只指自身。本镜像复用现有本地快速处理和外部Hivision；可选本地AI以依赖检测为准。模拟测试不代表真实人像效果。

网页只查询GitHub正式Release并展示发布页，不拉镜像、不挂Docker socket、不停止服务。日常更新由运维主动执行pull/up；固定2.4.1-rc.3标签以后升级需改目标版本。镜像公开可匿名pull；私有包使用read:packages凭据docker login，不将凭据放源码或Compose。包管理：https://github.com/users/HankNovic/packages/container/package/studentphotoflow 。日志滚动3×10MB，健康检查/重启策略见compose.yaml。

## 任务计时与 Hivision 并发

处理配置的“请求并发数”为1–16整数，缺省1。保存后立即影响每个服务地址的后续调度，各届任务、单人正式任务和单张试处理共享同一实例限额。降低限额不取消在途请求，须待在途数量低于新上限才发送下一项；任务照片参数快照不变。相同届次仍保留一次一个正式任务的原保护，不引入多进程或多副本。单次调整照片参数不能覆盖全局并发上限。配置JSON缺字段时兼容默认1，调高并发不保证提速。

暂停/安全结束收到请求即停止新调度，等待所有在途项目（包括失败/超时）收尾并保存。手动结束不再继续。处理请求有配置超时，不自动重试。总历时包含暂停及停机，实际运行时长按任务运行区间累计，不相加各学生耗时；每秒原子保存运行检查点。异常退出只使用已落盘时长，并提示是已确认下限，最多可能漏记最后检查点后的尾段；服务停机不计入运行时长。不确定结果仍须核对。历史缺失计时显示未记录；旧任务恢复后只能报告恢复后的部分时长。

本轮并发性能/错误/停止时序使用独立模拟服务，不代表真实Hivision性能或人像效果。网页更新检查继承2.4.0只查询正式Release的限制，候选升级请以本Release固定镜像为准。
