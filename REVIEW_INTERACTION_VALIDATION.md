# 人工审核页交互整理验收

开发验收日期：2026-09-17。基线 v2.4.1-rc.2（应用源码9b7d3c0），功能分支 feature/task-timing-hivision-concurrency，开发验收时HEAD为04cb9a18d98dffe6e4461c29ff5b5283be30daf3。
开发验收时修改未提交、未推送，未更改版本文件，未创建镜像或发布。实际测试构建标识 review-local-20260917-final，应用版本仍为2.4.1-rc.2；本地修改版不冒充已发布rc.2镜像。

## 已实现

- 使用sources/results、status、result.review、delivered/replacement、关联任务和prepared批次决定操作显示，不新增持久化业务状态。
- 上传按阶段区分上传照片/更换原始照片/上传替换照片；未实际交付不显示替换入口。已有待交付包不再重复生成。
- 审核只针对当前正式成片。待审核显示通过/退回；已有决定显示撤回；通过后进入交付区。下载与确认实际交付分开。
- 删除无绑定复选框；后端new_version参数、默认false、相同结果跳过、已交付保护和旧页面调用全部保留。
- 临时预览与正式阶段分开；上传、参数变化、正式结果/审核变化、开始正式处理会清除过期临时预览。试处理失败不继续显示旧成功图。
- 问号提示可键盘聚焦；异步操作有loading、防重复和持续反馈。上传回调捕获异常；重复原图提示未新增版本；替换成功提示下一步。
- 提交成功后刷新失败，显示“操作已提交，但页面刷新失败”，禁止在详情失效时继续修改，并提供刷新入口。
- 请求ID优先randomUUID，回退getRandomValues生成UUID；均不可用时计划不可执行，不发送无编号正式任务。检查学生、届次、请求序号和配置，拒绝过期响应。
- 导航保存打开时筛选顺序，审核后从筛选结果消失也不会令下一张跳回首项；导航不写跳过记录。
- 最近三条关联任务默认折叠，中文状态明确说明整批进度，折叠不影响轮询；版本与操作记录继续默认折叠。
- 仅本次参数中隐藏全局地址历史增删，全局处理配置保留管理入口；仍复用原配置组件。

## 阶段与主要操作

| 真实阶段 | 主要可见操作 |
|---|---|
| 无原图、未交付 | 上传照片 |
| 有原图未生成正式成片 | 更换原始照片、临时参数、检查计划、开始生成正式成片、试处理 |
| 正式成片待审核 | 制作相关操作（遵守相同结果跳过规则）、通过、退回 |
| 人工退回 | 更换原图/参数、检查计划、试处理、撤回审核；不自动重做 |
| 审核通过待交付 | 生成单人交付包、撤回审核 |
| 已有待确认包 | 下载、单人确认；多人包提示前往照片交付页统一确认/取消 |
| 实际已交付且未替换 | 启动替换流程、下载已有包、查看记录 |
| 替换中 | 上传替换照片和符合当前结果状态的制作/审核操作；历史交付保留 |
| 活动/暂停/结束中任务、结果不确定或归档 | 具体锁定原因，隐藏修改、审核及新交付操作，历史可查看 |

导航独立显示，根据边界、固定届次及提交中状态禁用。没有可执行计划时，“开始生成正式成片”禁用并显示原因。

## 实际验证

使用项目既有Python虚拟环境、独立目录review-interaction-20260917/app-verified，由launch_docker.py启动相同FastAPI系统，提供生产编译的Vue静态页面，不是Vite开发服务器。监听127.0.0.1:18996，独立数据目录review-interaction-20260917/data。正常管理员登录建立Cookie，独立headless Chromium，不接管用户Edge。

- **后端：168项通过。** 13个现有测试模块逐模块独立进程运行。初次捆绑Python缺fastapi，随后复用项目虚拟环境；单进程全套遇到Tcl_AsyncDelete线程退出错误，逐模块运行全部通过，没有安装或升级依赖。
- **前端单元：3项通过。** 阶段矩阵、UUID原生/回退/不可用/唯一性、无效复选框与请求参数检查。
- **生产构建通过。** 存在原有大bundle警告，未做无关拆包优化。
- **新增浏览器14组通过。** 覆盖全部主要阶段和tooltip，上传失败/重复照片/loading，双击计划只发一次，参数变化丢弃迟到响应，临时/正式处理，退回/撤回/批准，打包下载确认，替换，筛选变化导航，历史折叠和轮询，全局配置入口保留。
- **UUID兼容实测。** 在独立浏览器禁用randomUUID，实际正式任务使用回退ID。再禁用getRandomValues，明确报错，断言没有计划请求且不能开始任务。
- **故障反馈实测。** 上传/计划/试处理拒绝、审核已提交后GET失败使用受控HTTP响应。慢计划期间注入参数变化验证过期保护；不声称是生产现场故障复现。
- **真实应用流程。** 原图→临时预览→正式处理→审核→生成包→下载→确认使用真实接口。none引擎处理合成色块照片；本地Hivision模拟服务用于异步任务锁定，不调用生产服务。
- **既有浏览器6组回归通过。** cohort-acceptance.cjs更新新文案与阶段顺序，在最终构建验证届次校验、名单/单人处理、审核交付、替换、回收站恢复、退出登录。
- 其他旧Docker/Windows EXE专用历史脚本未逐一执行，不声称所有历史浏览器脚本通过。
- 下载包含可读00001.jpg及manifest.json；未修改照片命名规则。浏览器未捕获异常数量为0。
- 前端源文件SHA256与最终运行副本一致；final-build.json记录源码/静态文件校验值，final-verification.json记录最终结果。

## 开发验收时的 Docker 限制及未验证项

Docker路径：C:/Users/nivaya/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe。
docker version实际错误：

```text
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine
open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.
```

已尝试隐藏启动已安装的Docker Desktop，再执行docker desktop start --timeout 30，返回：

```text
Failed to start Docker Desktop
Docker Desktop is still starting: context deadline exceeded
```

因此没有构建或运行新Docker镜像，按本次明确允许的本地等价环境完成页面验收。Docker特有环境、真实远程HTTP来源的兼容现场、真实Hivision质量和性能未验证；能力缺失通过受控浏览器验证。没有生产数据调用。

## 未改接口和业务

POST students/{sid}/photos、students/{sid}/replacement、students/{sid}/preview、processing-jobs、reviews、deliveries、deliveries/{id}/confirm及GET学生/任务/交付/下载保持原实现和格式。
没有修改v2/api.py、service.py、store.py、photo_pipeline.py、workspace.json格式、批量页面或旧版页面；不删除历史、不调整人工退回后的重复处理规则、不强制new_version=true。

## 文件与证据

- 修改StudentDetail.vue；ConfigFields.vue、HivisionUrlPicker.vue增加仅本次区域的可选限制，全局默认行为保留；服务连接测试防重复并清空旧反馈。
- 新增review.js（展示条件/请求ID）、ActionHelp.vue（问号tooltip）。
- 新增tests/review-state.test.mjs、tests/review-interaction.cjs；更新tests/cohort-acceptance.cjs的文案、阶段步骤和独立测试届次。
- 任务清单：TASKS_REVIEW_INTERACTION.md。
- 独立证据目录：C:/Users/nivaya/Documents/Codex/review-interaction-20260917。
- review-01-missing.png至review-08-history.png，另有09-history-collapsed、10-history-expanded、11-tooltip，共11张真实浏览器图，均为合成素材。
- review-screenshots.zip仅含这11张图，不含令牌、工作区或原始学生附件。

开发验收结束时尚未创建commit、Git标签、镜像或远程发布，未操作生产部署。

## rc.3 发布归档

随后获得 v2.4.1-rc.3 候选发布授权；本次归档不再修改功能。五个前端源文件 SHA256 与上述最终测试清单一致。版本文件、Docker构建参数、Compose及示例环境统一为2.4.1-rc.3，并先提交再构建。发布阶段Docker Linux amd64引擎已恢复；构建、最终镜像验证及registry digest以Release所附发布验收记录为准，不能将前面的本地页面截图称作rc.3容器截图。保留历史rc，不合并main，不更新stable/latest。
