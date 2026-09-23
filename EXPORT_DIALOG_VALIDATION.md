# v2.4.1-rc.5 单人格式选择与预览分页修复

开发基线 v2.4.1-rc.4 / b71928c3d3f0c2277e7012a969c642ed3577c7a9；本报告记录本轮修复和本地验收，发布材料另行记录最终提交和镜像 digest。

- [x] 调查：单人 StudentDetail.deliver 未传 profile_id 直接生成；批量预览在 Students.vue，格式管理及历史交付在 Deliveries.vue。
- [x] 找到分页来源：Students.vue 的 planPage=1 / planSize=20，[20,50,100]，el-pagination，历史 a353dba。无全局持久化行数配置。
- [x] 先补测试并在原代码上复现失败。
- [x] 共用单人/批量交付预览，固定届次和学生范围，加载 active 格式并传 profile_id。
- [x] 从执行计划提取同一分页组件，沿用默认/选项和本地状态策略。
- [x] 后端保护测试与必要修复；真实 ZIP/manifest 核验。
- [x] 编译后 Vue + 相同 FastAPI 独立工作区浏览器验收。
- [x] 最终差异、测试、截图与限制记录。

沿用 Service.delivery 和 export_preview：POST /deliveries/preview、POST /deliveries 均接收 student_ids / profile_id；系统按 /cohorts/{id}/api/v1 路由到独立 Store。不得另写文件名解析器。原格式列表在 workspace.initialize/changeCohort 和 Deliveries.load 加载；新交付弹窗打开时从同一 export-profiles API 刷新，以免使用过期列表。

## 调查与修改位置

| 文件与位置 | 实际修改 |
|---|---|
| `v2/web-vue/src/components/StudentDetail.vue:128` | `deliver()` 从直接创建改为打开共用弹窗。成功后刷新原人工审核窗口，交付记录显示格式名称及 revision，保留原下载和确认入口。 |
| `v2/web-vue/src/components/DeliveryPreview.vue:26` | `show()` 固定当前届次和学生 ID，重新读取该届次 active profiles，默认选择默认格式。选择项显示名称、模板、revision。 |
| `DeliveryPreview.vue:17` | `preview()` 调用原预览 API；序列号阻止晚到响应覆盖当前选择。请求失败明确提示并禁止确认。 |
| `DeliveryPreview.vue:39` | `confirm()` 重新检查当前预览，内容变化时要求重新确认；正式提交含选定 `profile_id`，loading 和同步 guard 防重复。取消仅关闭，不创建交付批次。 |
| `v2/web-vue/src/views/Students.vue:34` | 批量入口复用同一弹窗；完整学生选择独立于分页；成功导航照片交付。 |
| `v2/web-vue/src/components/PagedTable.vue:5` | 提取原处理计划的 `planPage=1`、`planSize=20`、`[20,50,100]` 及 `el-pagination` 布局，局部字段变为 `page` / `size`。两处弹窗使用同一组件。 |
| `DeliveryPreview.vue:59` | 弹窗最大高度为可视区域减 32px；页头、页脚不收缩，主体可滚动；表格最大高度 40vh，少量数据按内容自然缩短。 |
| `v2/service.py:527` | `Service.delivery()` 在原锁与状态校验中补齐学生不存在/不属于当前届次的检查，和预览保持一致，在写交付数据前拒绝。 |

分页来源可用 `git show a353dba:v2/web-vue/src/views/Students.vue` 及基线文件核查。原先就是页面局部状态，没有全局行数配置、localStorage 或工作区字段。本次没有增加另一套持久化“预览行数”。更换格式回第一页并保留当前 page size；再次打开新选择重新从第一页显示。处理计划也继续使用相同默认值与选项，并在重新计算内容后重置页码。

文件名仍由 `v2/export_profiles.py` 在服务端渲染；没有新增前端命名解析器。预览/创建接口的请求结构、照片处理、审核业务规则、`new_version`、workspace schema 和已有交付文件格式均未改变。旧 manifest 没有快照时显示“历史学号命名格式”。

## 测试先行证据

在实现前添加 `tests/test_export_names.py` 两项交付保护测试和 `v2/web-vue/tests/export-dialog.cjs`，使用基线源码及编译后的 Vue 复现：

- 单人点击直接生成，等待“选择导出格式并预览文件名”弹窗失败。
- 批量 70 条预览实际渲染 70 行，断言预期 20 行失败。
- 构造不属于当前届次的学生，正式交付返回 200，预期 409，后端测试失败；预览本来已有此检查。
- 原代码测试截图：独立目录 `C:/Users/nivaya/Documents/Codex/export-dialog-before-20260921/failure-single.png`、`failure-bulk.png`。

实现后上述失败修复。浏览器脚本对 Element Plus 动画结束后测量几何范围，避免过渡动画中的临时位置造成误判。

## 最终本地验收结果（2026-09-21）

独立目录：`C:/Users/nivaya/Documents/Codex/export-dialog-final-20260921`。

使用 74 名合成学生、两个届次、真实 JPEG 字节及明确标注的合成已审核结果。前端通过 Vite 编译，使用本轮后端副本启动相同 FastAPI 系统服务；端口 19047，构建标识 `b71928c-dialog-fix-local`，版本保留 `2.4.1-rc.4`。这是未提交修改的本地验收构建，不是新发布产物。

| 用户测试编号 | 验证结果及证据 |
|---|---|
| 1–3 | 浏览器加载 3 个 active 格式，不显示停用格式；默认选中学号格式，可以选择姓名格式。 |
| 4–8 | 从人工审核窗口选择姓名格式，预览 `00123-张三.jpg`；捕获创建请求含 `profile_id: named`，点击页面下载按钮获得真实 ZIP，解压验证同名 JPEG 和 `format_snapshot` 的 `named` / revision 2。原详情及交付页显示“姓名格式 v2”。 |
| 9 | 无姓名学生预览显示“缺少字段：name”，确认按钮禁用；接口测试同时验证正式创建返回 409 且无交付写入。 |
| 10 | 已有 prepared 包时，再请求默认格式仍返回 409；不因切换格式重复创建。 |
| 11 | 取消单人预览前后批次数完全一致。 |
| 12 | 快速双击确认，只捕获一次创建 POST，只产生一个批次。 |
| 13–16 | 70 人列表默认 20 条，切换为 50 条；表格内部滚动；1366×768、1280×720、1024×600 下页头及确认按钮均在可视区域。使用同一 `PagedTable` / Element Plus 分页。 |
| 17 | 第一页显示“共 70 条，当前显示第 1–20 条”；翻页 21–40；50 条第二页 51–70。 |
| 18 | 切换格式回到第一页且保留已选 50 条；重新选择一人显示 1–1；预览期间将选中合成学生移入回收站，再预览返回明确当前届次错误并禁止生成，之后通过原恢复接口恢复。 |
| 19 | 单人及少量列表按实际行数缩短，不撑满大表格；`07-small-preview.png`。 |
| 20 | 原执行计划 70 条数据默认显示 20 条，翻到 21–40 后重新计算回到 1–20；背景学生列表没有修改数据范围与选择规则；审核状态和 UUID/new_version 前端回归测试 3 项通过。 |

补充验证：

- 70 人批量在第二页确认，创建请求仍包含完整 70 个学号，实际 ZIP 为 70 张 JPEG，全部文件名与预览一致，不只导出当前页。
- `张/三` 显示替换提示并预览为 `00125-张_三.jpg`；`张/三` 与 `张:三` 使用仅姓名格式产生重复，预览列出两个学号并阻止生成。对应正式创建拒绝由后端测试覆盖。
- 预览后将该格式停用，确认会重新检查并显示停用错误，不生成批次；另一届次无法使用该 profile。
- 人为延迟旧格式响应，切换回学号格式，晚到姓名响应不覆盖当前预览。模拟 503 预览请求失败可见且确认禁用。这是网络异常模拟，不是 Hivision 测试。
- 后端测试覆盖 prepared → cancelled、再次生成 → delivered，以及旧 manifest 读取和旧 ZIP 字节不变、默认学号命名、缺姓名/重名拒绝。

执行结果：

- Python `unittest test_export_names test_v2 test_system`：**41 项通过**。
- `tests/test_export_profiles.py` 自检：通过（2 个检查函数）。
- `node --test v2/web-vue/tests/review-state.test.mjs`：**3 项通过**。
- `node tests/export-dialog.cjs <独立目录>`：单人、批量、异常场景全部通过，无浏览器 pageerror；`browser-all.json` 记录两个实际创建请求及空错误列表。
- `tests/verify_export_dialog.py <独立目录>`：对已下载 ZIP 进行真实解压、逐张 Pillow JPEG 校验和原始字节比较，**1 张单人 + 70 张批量**全部通过；保存独立 manifest 副本及 `zip-verification.json`。
- Vue production build 通过；只有原有大于 500KB 的 bundle 提醒。`git diff --check` 通过。

复现命令（Python 使用项目 `.portable-build/venv/Scripts/python.exe`）：

```powershell
# 先创建全新的独立目录，脚本拒绝复用已有学生工作区
python tests/prepare_export_dialog.py <新的验收目录>
# 在 v2/web-vue 设置 SPF_VUE_OUT=<新的验收目录>/app/v2/web，执行 npm run build
# 单独终端运行 python <新的验收目录>/server.py
node v2/web-vue/tests/export-dialog.cjs <新的验收目录>
python tests/verify_export_dialog.py <新的验收目录>
```

## 截图与 ZIP 证据

以下文件在上述 final 独立目录，未放入 Git 工作区；全部为合成数据截图：

| 文件 | 内容 |
|---|---|
| `01-single-selected.png` | 人工审核窗口单人姓名格式与真实文件名预览 |
| `02-single-generated.png` | 单人生成后原审核窗口的格式、下载与交付入口 |
| `03-delivery-format.png` | 照片交付页格式名称与 revision |
| `04-missing-name.png` | 缺姓名阻止 |
| `05-bulk-page1.png` / `06-bulk-page50.png` | 70 人分页，20／50 条及总数 |
| `07-small-preview.png` / `08-short-window.png` | 小列表自然高度与 1024×600 下可见页脚 |
| `09-illegal-character.png` / `10-collision.png` | 非法字符替换提示、替换后重名阻止 |
| `single-selected.zip` / `single-selected.zip.manifest.json` | 单人真实页面下载：`00123-张三.jpg`，`named` revision 2 |
| `batch-selected.zip` / `batch-selected.zip.manifest.json` | 批量 70 人：`00200-合成姓名200.jpg` 至 `00269-合成姓名269.jpg` |
| `batch-preview.json` / `zip-verification.json` / `browser-all.json` | 实际 API 预览、逐张 ZIP 校验和浏览器请求证据 |

## 范围与未验证项

- **未完成 Docker 实测**，本轮没有启动或构建 Docker 镜像。
- 没有调用真实 Hivision，也没有进行人像处理效果验收；图片是本地生成的合成 JPEG，处理结果为测试夹具。
- 本轮两处功能及本地等价验收已完成，没有待处理的功能阻塞。
- 未修改真实学生资料、原工作区或既有交付文件。测试删除/恢复仅针对独立合成工作区。
- 验收结束已停止本轮独立 FastAPI 测试进程，保留工作区与全部验收证据。
- 未提交 commit；HEAD 仍为 `b71928c3d3f0c2277e7012a969c642ed3577c7a9`。当前 diff 为 4 个已跟踪文件修改及 6 个新增源码/测试/报告文件。未暂存、未建 tag、未推送、未建镜像、未发布。后续 rc.5 等用户确认。
