# 五阶段实施与验收

当前版本 v0.5.0。五阶段的本机实施范围已完成；iOS／macOS 连接入口保留，因没有 Mac 主机，实际设备验证仍是明确例外。本表区分已实现功能与验证范围，不把后续扩展项或缺少设备的测试记为通过。

| 阶段 | 实现范围 | 验证 |
| --- | --- | --- |
| Phase 1：通用内核 | 项目／目标／用例／套件／环境／调度、Test IR、API／Web／受控 CLI、断言、队列、并发／取消／重试、报告／历史／证据、CLI／API／界面、独立文件插件 | CLI 两次各 22 PASSED，Web 22 PASSED；诊断用例按预期 FAILED／ERROR；调度重启、CRUD、主题、宽度通过 |
| Phase 2：领域与数据扩展 | 只读 SQLite、TCP、MQTT／IoT 协议、数据驱动、HTTP 基础性能、Cron／时区／重叠／补跑、环境目标覆盖、CSV／JSON 数据集 | 数据库／TCP／性能套件两次 CLI 与 Web 各 9 PASSED；MQTT 两次 CLI 各 4 PASSED，真实日历与远程节点 4 PASSED；数据／环境远程 2 PASSED |
| Phase 3：可选 AI | Provider、自然语言／用例生成、失败分析、覆盖／修复建议、报告总结，候选校验与人工应用；默认关闭 | Provider 协议、建议工作流和界面在完整测试中通过；AI 关闭时所有核心验收照常执行。未配置真实云模型密钥，不宣称完成云模型效果评估 |
| Phase 4：设备与可靠性 | Android、Windows、iOS／macOS Appium 连接协议、protobuf gRPC、分阶段负载、HTTP 故障恢复、自定义插件 | gRPC／性能／可靠性 CLI 两次及 Web 各 5 PASSED；Android 模拟器 1 PASSED、真实 Windows 窗口 1 PASSED；Apple 实测保留待设备验证 |
| Phase 5：平台工程化 | 多节点任务、持久化租约、证据回传、细粒度项目角色、成员／团队、审计／运维、备份恢复、插件版本与 SDK、完整 wheel 和 CI 流程 | 节点／取消／失联／重启、项目与角色隔离、成员轮换／停用／团队撤权、恢复和独立安装通过；远程节点按同机独立进程实测 |

本次完整后端测试 **183 passed**，第三阶段专项 **12 passed** 已包含其中；不重复累加。具体结果见 [验收记录](verification.md)。

## 验证入口

```bash
pytest -q
python scripts/acceptance.py
python scripts/acceptance_phase2.py
python scripts/acceptance_phase4.py
python scripts/acceptance_phase5.py
python scripts/acceptance_nodes.py
python scripts/acceptance_protocols.py
python scripts/acceptance_completion.py
```

原生设备使用 `scripts/acceptance_devices.py`，按 [第四阶段文档](phase4.md) 配置主机环境与管理员 profile。脚本默认端口可能与已启动工作台或 fixture 冲突，运行前检查端口或使用对应参数指定空闲端口。全部脚本执行真实测试，汇总只读取实际报告。

源码工作流已加入 Ubuntu／Windows 检查、wheel 独立安装和 Linux 容器验证。当前增量在 Windows 本地完成收尾验收；新的远程 CI 尚未触发，具体提交结果以 Actions 记录为准。

## 保留边界

本轮不把产品路线中的每一种未来技术都当作阶段结束的前置条件。PostgreSQL／MySQL、Web 录制、图形化步骤编排、Allure、流式 gRPC、大规模压测、系统级 chaos、多主协调、自动迁移重试、SSO／租户 OS 隔离等继续作为后续扩展，不宣称已实现。

多节点当前是单协调服务加固定节点分配，不共享 SQLite、不自动重放失联任务。用户与团队提供管理 API 权限，不能替代操作系统隔离。Apple 设备需要实际 Mac；安装 Windows 软件不能替代 Xcode 或 Apple 驱动主机。

详细结果及原始数据位置见 [验收记录](verification.md)。
