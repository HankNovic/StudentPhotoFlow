# 姓名字段闭环任务与验收

日期：2026-09-20。分支 feature/task-timing-hivision-concurrency；HEAD 为 df320e6bee0504cdc584c6d0911b7d4dd10ff563（v2.4.1-rc.3）。报告对应基线之上的未提交工作区，不能用该提交号单独复现本轮实现。保留已有未提交导出格式功能。

## 完成清单

- [x] 调查学生、Excel、手工名单、ZIP、详情、模板、预览、交付与 manifest。
- [x] 可选 name 字段、Excel 姓名列及整批冲突拒绝；手工名单/ZIP 保留姓名。
- [x] 姓名模板、缺失阻止、非法字符、重名完整接口验收。
- [x] 编译后 Vue 实际导入、列选择、预览、缺失提示及单人/批量下载。
- [x] 打开真实 ZIP/JPEG，核对照片字节、manifest、旧格式和旧记录。
- [x] 回归、构建、差异及最终证据核对。

初始实现先于完整测试落盘；续作先补失败回归再修复已交付学生姓名更新、列选择及 Windows 重名缺陷，不声称全部严格按 TDD 顺序完成。

## 数据与导入规则

学生保留 id、cohort、sources、results、approved、delivered、replacement、history，新增 name: string | null。字符串学号仍为唯一主标识，schema_version 仍为 2。旧学生加载时内存补 name=None，仅加载不重写 workspace；旧 ZIP/manifest 不改写。姓名不参与处理、审核或交付状态判定。

Excel 复用 WorkbookReader 列映射，新增可选姓名列。检查返回识别列、空姓名行号及冲突；手选列优先且更改列使旧检查失效。没有姓名列的旧表可导入；空姓名行提示但不因此阻止整批。任务创建时名单、非空姓名、任务一并保存：新学生保存姓名；非空导入值更新已有姓名；空值不覆盖。元数据更新不绕过照片保护。一个文件同学号不同姓名整批拒绝，列出学号、姓名、行号；同名重复行仍沿用原重复学号拒绝规则。

手工名单仍为一行一个学号，并提示通过 Excel 补姓名；不覆盖姓名。ZIP 沿用学号识别，不从 00123-张三.jpg 猜姓名，不覆盖姓名。未新增多列名单解析器。

模板支持 {student_id}、{name}、{ext}，扩展名来自实际审核成片。缺姓名阻止预览和交付，不产生 00123-.jpg。非法字符替换为 _ 并提示，文件名末尾空格/句点移除；处理后重名及 Windows 大小写重名阻止导出，不追加序号。class_name、grade、major 仍拒绝保存和导入。

## 测试与需求映射

已有 Python 环境：.portable-build/venv/Scripts/python.exe。Python 测试合计 66 项通过：test_export_names.py 15、test_v2.py 15、test_system.py 9、test_delivery_locks.py 20、test_roster_overview.py 7。test_export_profiles.py 自检、Python 编译、Vue 生产构建及 git diff --check 通过；保留已有 bundle 大小与 Git 行尾提示。未安装或升级依赖。

用户要求的 16 项由 tests/test_export_names.py 覆盖（下列方法省略 test_ 前缀）：

| 要求 | 测试方法 | 结果 |
| --- | --- | --- |
| 1 旧 workspace 缺 name | old_workspace_and_name_import_rules | 通过，读取前后文件字节不变 |
| 2 姓名保存 | excel_import_persists_name_and_empty_does_not_overwrite、selected_mapping_checked_and_imported | 通过 |
| 3 无姓名列旧表 | actual_excel_old_format_update_and_manual_roster_preserves | 通过 |
| 4 空值不覆盖 | 同上、delivered_name_update_preserves_photos_review_and_old_package | 通过 |
| 5 同批姓名冲突 | conflict_rejects_entire_batch_without_side_effects | 通过，整批无写入 |
| 6 真实姓名文件名 | name_template_real_single_and_batch_zip_and_manifest | 通过 |
| 7–8 缺失姓名预览/交付失败 | missing_name_blocks_preview_and_delivery_without_writes | 通过 |
| 9 非法字符替换 | name_template_real_single_and_batch_zip_and_manifest | 通过 |
| 10 处理后重名 | sanitized_and_windows_case_collisions_block_delivery | 通过 |
| 11–13 单人/批量 ZIP 与 manifest | single_batch_zip_bytes_and_manifest | 通过，真实 JPEG/ZIP |
| 14 旧 manifest | legacy_manifest_read_download_do_not_rewrite | 通过，不重写 |
| 15 旧学号模板 | name_template_real_single_and_batch_zip_and_manifest | 通过 |
| 16 未实现字段 | json_roundtrip_and_unimplemented_fields_rejected | 通过 |

另有 zip_does_not_parse_or_overwrite_name 验证 ZIP 不猜/覆盖姓名。

## 浏览器与实际下载证据

独立本地等价环境：编译后 Vue + 同一 FastAPI 服务，端口 19043，六名合成学生及真实合成 JPEG。照片使用既有本地处理流水线（background_mode=none），未请求 Hivision。测试脚本为 v2/web-vue/tests/export-names.cjs，复用已有 Playwright/Chromium。

浏览器通过：首次检查前手选 B 学号/C 姓名/D 照片并复查；空姓名行提示；导入后列表/详情姓名；同批冲突拒绝；无姓名列旧表；本地处理及批量审核；创建/默认姓名模板；正常和非法字符文件名预览；缺姓名显示错误并禁用生成；详情生成并下载单人包；批量生成下载；旧格式导出无姓名学生。未捕获未处理页面异常。

证据目录：C:/Users/nivaya/Documents/Codex/name-export-validation-20260920-final。

最终 build ID：40bebceb-be9c-47ec-8685-b2ab026dfcf3，资源 assets/index-BnZCfTSa.js，前端与服务标识一致。此为未提交开发构建，构建字段 version=development、source_commit=unknown；build-receipt.json 另存基线 HEAD 和 uncommitted=true，不冒充发布源码或镜像。

| 下载包 | 实际照片文件名（另含 manifest.json） |
| --- | --- |
| single.zip | 00123-张三.jpg |
| batch.zip | 00456-李四.jpg、00789-张_三.jpg |
| legacy-id.zip | 00888.jpg |

verify_zip.py 已打开 ZIP/JPEG，核对成片字节相等；zip-verification.json 保存 SHA256 及 manifest。每项包含 student_id、原始 name（如 张/三）、实际 file_name（00789-张_三.jpg）、result_id、source_id；顶层 format_snapshot 保存格式 ID、名称、模板、修订和规则。三个下载批次仍为 prepared，下载未自动确认交付。

截图：01-excel-name-selection.png、02-students-with-names.png、03-name-conflict.png、04-name-template.png、05-missing-name-blocked.png、06-name-preview.png、07-single-delivery.png、08-batch-preview.png、09-batch-delivery.png、10-legacy-id-format.png。机器记录：browser-results.json、browser-records.json、zip-verification.json、build-receipt.json。证据中的 token.txt 为私有测试凭据，不得提交或分享。

## 关键位置

- v2/store.py:57：Store 加载兼容及名单创建。
- xlsx_photo_core.py:504、648：原解析器的可选姓名列与检查。
- v2/api.py:502：Excel 检查/导入接口；学生列表返回姓名。
- v2/service.py:293：原子保存名单/姓名/接收任务；512 预览、527 共用交付及 manifest。
- v2/export_profiles.py:29：字段渲染、缺失错误、文件名及重名校验。
- v2/web-vue/src/views/Import.vue、Students.vue、Deliveries.vue：列选择反馈、姓名显示、模板字段。
- tests/test_export_names.py、v2/web-vue/tests/export-names.cjs：接口和实际页面验证。

## 边界与工作区状态

范围内无未完成项。Docker、真实 Hivision、人像视觉质量未验证，不作通过声明。未扩展班级/年级/专业、手工多列名单、ZIP 姓名猜测；未改人工审核或 new_version。单人详情复用交付页的默认格式，未另加格式选择器。

HEAD 未变，暂存区为空，保留已有导出功能及本轮姓名闭环的未提交 diff。无 commit、tag、push、新镜像或 Release 操作；未操作生产工作区或原始附件。
