> 历史记录（2026-09-18）。姓名字段与导出格式的当前验收以 NAME_FIELD_VALIDATION.md 为准；本文件中的 00123-.jpg 仅是历史学号模板示例，不代表姓名导出通过。

# 可客制化照片导出格式：实现与本地验收

> 历史记录（2026-09-18），字段现状与验收结论已由 [姓名字段闭环验收](NAME_FIELD_VALIDATION.md) 更新。下文保留当时范围，不代表当前实现：现已持久化姓名并支持 `{name}`。历史 `00123-.jpg` 只验证连接符拼接，不是姓名导出验收；当前姓名缺失会阻止预览和交付。

日期：2026-09-18。基线为 v2.4.1-rc.3 / `df320e6bee0504cdc584c6d0911b7d4dd10ff563`。本次只做开发、测试和本地等价验收，未创建镜像、标签、推送或 GitHub Release。

## 字段调查结论

当前 `Store.add_roster()` 和学生对象只持久化 `id`（学号字符串）、`cohort`、照片源/成片、审核、交付和历史记录。`xlsx_photo_core` 的导入行最终也只以 `student_id` 建立名单；TXT/ZIP 导入同样没有姓名、班级、年级或专业的持久化字段。没有静默生成姓名等空字段。

当前可安全进入模板的字段：`{student_id}`、`{ext}`。`{ext}`取实际审核成片 artifact 的真实扩展名（例如 `.jpg`）。`{name}`、`{class_name}`、`{grade}`、`{major}` 等未知字段会在保存和导入时拒绝。批量学生交付和人工审核窗口的单人交付都调用同一个 `Service.delivery()`，因此使用同一套预览、校验、命名和 manifest 快照逻辑。

## 实现范围

- `v2/export_profiles.py`：模板字段解析、非法字符替换、Windows 保留名/长度/重名/缺失字段检查；默认模板 `{student_id}{ext}`。
- `v2/store.py`：schema 2 工作区缺少 `export_profiles` 时自动提供默认格式，不清空或重写旧交付数据。
- `v2/api.py`：格式列表、新建、修改（保留 `history` 和 revision）、复制、默认、停用/恢复、删除保护、JSON 导入/导出、交付文件名预览；`POST /api/v1/deliveries` 增加可选 `profile_id`。
- `v2/service.py`：`delivery()` 对实际成片扩展名重新渲染模板，失败时不创建交付记录；ZIP 根目录使用真实文件名，manifest 保存 `format_snapshot` 和每项 `file_name`。
- `v2/web-vue/src/views/Deliveries.vue`：导出格式管理、普通字段按钮/高级模板、导入导出 JSON、修订和停用显示、旧批次“历史学号命名格式”显示。
- `v2/web-vue/src/views/Students.vue`：批量交付先预览实际文件名，再确认生成；单人交付继续走同一后端默认格式。
- `v2/web-vue/src/workspace.js`：按当前届次加载格式，届次切换同步刷新。
- `tests/test_export_profiles.py`：核心模板和旧 workspace 兼容检查。

格式对象示例：

```json
{
  "id": "稳定本地标识",
  "name": "校园卡照片",
  "template": "{student_id}-{ext}",
  "revision": 1,
  "status": "active",
  "is_default": false,
  "rules": {"missing_field":"error", "invalid_character":"replace", "duplicate_name":"error"},
  "history": []
}
```

历史交付的 `manifest.json` 示例字段：`format_snapshot.profile_id`、`name`、`template`、`revision`、`rules`，以及每项 `file_name`。旧 manifest 没有快照时前端显示“历史学号命名格式”，不会重写旧 ZIP。

## 实际验证

- 核心 Python 自检：通过；模板解析、自定义连接符、前导零、真实扩展名、未知字段、非法字符、Windows 保留名、重名和超长模板覆盖。
- Python 编译：`v2/export_profiles.py`、`v2/store.py`、`v2/service.py`、`v2/api.py` 通过。
- Vue 生产构建：通过。保留既有大 bundle 警告，未做无关拆包。
- API + 真实 ZIP 等价验收：通过。合成学生 `00123` 的实际成片为 `.jpg`，模板 `{student_id}-{ext}` 生成 ZIP 文件 `00123-.jpg`；ZIP 根目录含该照片和 `manifest.json`，manifest 的格式快照和实际 `file_name` 均已检查。JSON 导出后重新导入到同一临时工作区生成“导入副本”通过。
- 旧 schema 2 workspace：缺少 `export_profiles` 时自动提供默认 `{student_id}{ext}`，不要求清空工作区。
- 浏览器：使用编译后的 Vue 页面和相同 FastAPI 系统本地等价环境，访问独立端口 18998、独立工作区，验证“照片交付 → 新建导出格式 → 保存 `{student_id}-{ext}` → 页面显示格式和模板”，并保存截图 `C:/Users/nivaya/Documents/Codex/export-format-browser-20260918/export-format-management.png`。未使用生产数据或 Hivision。
- `git diff --check`：通过。

## 未验证与边界

Docker 本轮未启动或构建新镜像，符合“不创建新镜像”的要求；因此不声称 Docker 实测通过。真实 Hivision、人像质量和生产工作区未调用。当前名单没有姓名/班级等字段，因此依需求明确拒绝这些模板字段；若要开放，需要先扩展现有名单导入结构并重新验收。单人交付没有另建流程，直接使用同一 `Service.delivery()` 默认格式；单人场景若要在弹窗内切换格式，可复用交付页的 profile 选择接口。

## 工作区状态

本次未提交 commit，未创建 tag，未推送，未发布 Release。仅保留当前工作区 diff；旧 workspace、旧交付记录和旧 ZIP 未修改。
