# 2.4.1 正式发布验证

## 范围与任务清单

基线 v2.4.1-rc.8 / 4ef80acdb7281b7a9f59f99d0aad04d742d8338a。接手时已有 7 个修改文件和 name-required.cjs，完整保留并复核，没有重复实现。新增浏览器正常接收与手选列验证。业务差异仅为现有 ActionHelp 配对样式共享、Excel 照片导入姓名必填。

- [x] 所有 ActionHelp 调用位置检查（Import、Students、StudentDetail、ConfigFields）；共用 action-pair：inline-flex、align-items:center、gap:6px。
- [x] Excel 检查 can_import 和正式接收入口共同依赖现有 inspect_selection 结果；缺列或空姓名时在启动任务、写学生前拒绝。
- [x] 保留手工名单、ZIP、旧工作区、图片处理、审核和交付逻辑。
- [x] 后端相关测试 49 项通过；全量 193 项通过。
- [x] Vue 构建及 git diff --check 通过。
- [x] 独立 linux/amd64 Docker 镜像浏览器登录、缺列/空姓名禁用接收、直接请求拒绝、空白姓名拒绝且学生不变。
- [x] 自动识别姓名/图片/学号不同列序，以及手选任意表头映射后，真实合成 JPEG 接收，姓名及照片落盘。
- [x] 历史登记确认框与问号间距 6px、中心差不超过 1px；学生和审核弹窗说明使用共用样式；无浏览器 JS 错误。
- [ ] 版本提交后的最终镜像验证、GHCR、main、stable/latest、正式 Release 与附件回读：发布后结果见同名 Release 附件，源码报告不预写成功。

## 命令与边界

```
$env:PYTHONPATH=(Resolve-Path tests).Path
.portable-build/venv/Scripts/python.exe -m unittest test_export_names test_v2 test_system
.portable-build/venv/Scripts/python.exe -m unittest discover -s tests
npm --prefix v2/web-vue run build
git diff --check
node v2/web-vue/tests/name-required.cjs <合成Excel及截图目录> http://127.0.0.1:19043
```

浏览器脚本只使用独立合成工作区和图片字节；SPF_TEST_TOKEN 通过环境变量传入。缺列、空姓名、空白姓名、自动识别（姓名/图片/学号）、手动选择（证件图片/编号/称呼）五份 XLSX 使用 tests/test_export_names.py 的 workbook() 生成。

193 项全量通过，无 cv2 缺失。模拟请求超时测试产生一次预期断开连接的服务端日志，测试仍通过；另有 Starlette 弃用提示及 Vite 500 kB chunk 提示。首次浏览器启动早于容器就绪返回 ERR_EMPTY_RESPONSE，健康接口可用后重跑通过，未以该失败记录充当验收成功。

未验证真实 Hivision 人像效果、arm64 或生产服务器；未连接生产、未操作 8770。旧工作区缺姓名继续兼容读取；严格规则仅针对新的 Excel 照片接收。部署、备份和回退见 DOCKER部署.md。
