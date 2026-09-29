# 2.4.0 正式发布清单
基线：v2.4.0-rc.8 / c4d41c606dee0e4620562e0ebdf3da26a7713646。仅版本与发布配置变更；保留业务实现。
- 完成：候选标签、源码版本、镜像revision核对；历史候选不修改。
- 完成：main快进合并全部功能分支历史，无冲突，并已推送。
- 完成：正式版本先提交，再从Git archive隔离构建；镜像源码和v2.4.0标签固定为dca895e968626ac9fb38c6e865a929cdee0111d5。
- 完成：Python依赖与rc.8逐项一致；31项后端测试通过。
- 完成：rc.8有数据副本升级，21名学生、8任务、5交付、1回收站和完整照片校验值保留；正式容器浏览器登录、分页、导入/本地处理/审核/交付冒烟。
- 完成：五项反馈行为复核；rc.8遗留的单人弹窗无绑定勾选框和计划布局限制明确记录，未冒充修复。
- 完成：原已验证镜像通过宿主机registry客户端上传成功，registry digest保持不变；Docker上传超时、Actions权限失败已如实记录。
- 完成：匿名拉取、最终digest启动登录/交付下载验证；2.4.0/stable/latest同digest；正式Release为latest，8个附件SHA256核对通过。
- 已记录限制：本机真实GitHub更新API返回403限流，界面正确失败提示且业务不受影响；GitHub Release/latest已独立核对。
- 正式镜像/标签源码：dca895e968626ac9fb38c6e865a929cdee0111d5。registry digest：sha256:5a394a3d4d1fd5b49a09ed1ffa0cc676d3c3275c45fdc2fb11d0920ec3f8c792。
- Release：https://github.com/HankNovic/StudentPhotoFlow/releases/tag/v2.4.0 。完整结果见RELEASE_240_VALIDATION.md。
- 限制：真实Hivision不重复调用；本轮使用合成素材、本地引擎及受控错误/更新模拟。没有连接生产部署。
