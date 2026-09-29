# 历史验收、验证与任务记录

这里归档各开发阶段留下的原始文档，保留当时的版本号、分支名、验收结论、未完成项及证据范围。文件名不一定等于最终发布版本，请以各文档正文为准；历史状态不代表当前功能或验证状态。

当前使用入口见 [项目 README](../../README.md)，安装、升级和回退见 [Docker 部署说明](../../DOCKER部署.md)。已发布 Release 及附件保持原样。

| 文档 | 内容 |
| --- | --- |
| [VUE3_MIGRATION_CHECKLIST.md](VUE3_MIGRATION_CHECKLIST.md) | Vue 3 / Element Plus 迁移及候选验收 |
| [FEATURE_ACCEPTANCE.md](FEATURE_ACCEPTANCE.md) | 届次、回收站、单人补录验收清单 |
| [RC4_ACCEPTANCE.md](RC4_ACCEPTANCE.md) | 2.4.0-rc.4 验收 |
| [RC5_TASKS.md](RC5_TASKS.md) | rc.4 使用反馈修复任务及后续候选状态 |
| [RC6_ACCEPTANCE.md](RC6_ACCEPTANCE.md) | rc.4 使用反馈修复验收与候选发布 |
| [RELEASE_240_TASKS.md](RELEASE_240_TASKS.md) | 2.4.0 正式发布清单 |
| [RELEASE_240_VALIDATION.md](RELEASE_240_VALIDATION.md) | 2.4.0 正式发布验证 |
| [TASKS_241.md](TASKS_241.md) | 任务计时与 Hivision 并发任务清单 |
| [RELEASE_241_VALIDATION.md](RELEASE_241_VALIDATION.md) | 2.4.1-rc.2 验收 |
| [TASKS_REVIEW_INTERACTION.md](TASKS_REVIEW_INTERACTION.md) | 人工审核窗口交互整理任务 |
| [REVIEW_INTERACTION_VALIDATION.md](REVIEW_INTERACTION_VALIDATION.md) | 人工审核窗口交互验收 |
| [EXPORT_FORMAT_VALIDATION.md](EXPORT_FORMAT_VALIDATION.md) | 导出格式早期开发验收，保留原有限制说明 |
| [NAME_FIELD_VALIDATION.md](NAME_FIELD_VALIDATION.md) | 姓名字段闭环验收 |
| [RELEASE_EXPORT_TASKS.md](RELEASE_EXPORT_TASKS.md) | 2.4.1-rc.4 导出格式与姓名闭环发布任务 |
| [EXPORT_DIALOG_VALIDATION.md](EXPORT_DIALOG_VALIDATION.md) | 单人格式选择及预览分页验收 |
| [RELEASE_241_FINAL_VALIDATION.md](RELEASE_241_FINAL_VALIDATION.md) | 2.4.1 正式发布前验证记录及证据边界 |

## 阅读说明

- 本次仅移动原文，不重写历史验收结论。相互引用的历史文件仍位于同一目录。
- 文中的源码路径、测试命令及未做成链接的 `DOCKER部署.md` 等根目录文件名，仍以仓库根目录为基准；测试命令不要从本归档目录执行。
- `VUE3_MIGRATION_CHECKLIST.md` 中原有验收报告链接指向作者电脑的 `C:/Users/...` 路径，不能作为 GitHub 可访问附件。保留原始引用，不补造或上传本地证据。
- 根目录的旧便携版使用说明、接口说明和独立导出说明暂留原位；其中有打包脚本或首页引用，本轮不扩大到旧工具和文档重写。
