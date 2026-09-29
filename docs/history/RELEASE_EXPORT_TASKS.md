# 2.4.1-rc.4 导出格式与姓名闭环发布

- [x] 审查差异及敏感数据；保留 rc.3 基线与历史。
- [x] 本地、远端 Git 无 rc.4；GHCR manifest 查询返回 manifest unknown。
- [x] 版本、Docker 构建参数、Compose、环境示例及部署说明统一。
- [x] 姓名/导出与回归 66 项通过，Vue 编译通过。
- [x] 源码、测试、原始验收报告提交；凭据与测试数据留在仓库外。
- [ ] 从最终提交的 git archive 构建 linux/amd64 镜像，校验完整源码 SHA。
- [ ] 最终镜像浏览器/接口/ZIP/JSON/历史验收。
- [ ] rc.3 有数据副本升级、最终容器重建和文件/业务记录比对。
- [ ] 推送分支和标签、GHCR 上传、匿名拉取。
- [ ] 上传白名单附件，创建 Pre-release，读回与回下载校验。

构建前任务状态记录于此；最终镜像、验收证据、完成状态与 registry digest 随 Release 的 RELEASE_VALIDATION.md 归档。原 NAME_FIELD_VALIDATION.md 和 EXPORT_FORMAT_VALIDATION.md 保留本地开发验收时间点，不冒充容器验收。
