# Phase 1 实际验收记录

验证时间：2026-10-24 16:37（Asia/Shanghai）。

环境：Windows、Python 3.12.15、Node.js 24.21.0、Chromium（Playwright 管理）。未配置模型或 AI API Key。

## 自动化检查

| 检查 | 实际结果 |
| --- | --- |
| pytest | 45 passed，11.69 秒 |
| Ruff lint | 通过 |
| Ruff format | 36 个 Python 文件格式检查通过 |
| 前端 Prettier | 通过 |
| TypeScript + Vite production build | 通过 |
| 独立 CLI 正常套件，第 1 次 | 22 PASSED |
| 独立 CLI 正常套件，第 2 次 | 22 PASSED |
| 诊断套件 | 1 FAILED + 1 ERROR，与预期一致 |
| 第四种文件插件 | PASSED |
| 保存定时配置、重启服务后自动触发 | PASSED |
| 通过管理界面运行完整套件 | 22 PASSED |
| 通过界面创建、复制和删除用例 | PASSED |
| 浅色与深色、1440px 和 820px 宽度 | 截图检查完成 |
| 浏览器脚本错误 | 0 |

pytest 额外覆盖：断言操作、JSONPath、Schema 校验、参数和凭据解析、未知操作、目标及文件范围、HTTP 写权限、跨项目引用、事务化导入回滚、运行快照与后续编辑隔离、历史恢复、中断任务处理、取消、超时重试、日志与文本证据脱敏、失败截图、调度持久化、单调度器锁、正则执行超时及核心不导入 AI。

pytest 输出包含 1 条 Starlette TestClient 对现有 httpx 测试集成的弃用提示；没有测试失败。运行时 HTTP Adapter 使用 httpx，当前兼容性已通过真实请求验证。

## 可复查证据

此次完整验收执行：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD/data/browsers"
python scripts/acceptance.py --data-dir data/release
```

- 数据库：`data/release/omnitest.db`
- 机器可读摘要：`data/release/summary.json`
- 两份完整 CLI 报告：`data/release/cli-pass-1.json`、`cli-pass-2.json`
- 界面截图：`data/release/dashboard-light.png`、`dashboard-dark.png`、`dashboard-tablet.png`
- pytest JUnit：`data/test-results.xml`
- 截图和文件证据：`data/release/evidence/`

正常套件两次运行 ID：`b213f04e09104750b74dedb0f539781a`、`dae8a4abd947443b869ab5bf5590c925`。

诊断运行 ID：`e168e3109606418e933720b5f3b40ef3`。

项目 ID：`01db98caac4b4992844e64ba40b34842`。

正常套件有 21 个定义，数据驱动展开后为 22 个执行结果。全部示例共有 24 个定义，不能将定义数量与运行结果数量混淆。诊断记录故意保留，因此 Dashboard 的总体通过率不是 100%。

## 验证边界

本机没有 Docker，因此未执行 Docker build／Compose；已提供可执行配置和中文说明。GitHub Actions 已配置 Linux 检查，但本次没有远程仓库，也没有实际触发 CI。Linux／macOS 尚未在本次环境实测。

首次发布后补充：主分支已在 GitHub 托管 Ubuntu 环境完成真实 CI，后端测试、前端检查／构建和端到端验收全部通过，见 [Linux CI 记录](https://github.com/Sver0411/OmniTest/actions/runs/40079631588)。上文“未触发 CI”描述的是发布前的本地验收时点。

当前为本地单管理员 MVP，提供间隔调度、JSON 配置编辑和受控程序执行；不含 cron、RBAC、分布式 worker、OS 级资源配额，以及完整的 Windows 子进程树隔离。具体限制见架构说明和 README。AI 只提供可选扩展契约，未实现模型调用。

## 2026-10-24 第四阶段增量验收

本节记录当前增量；上文是第一阶段验收时点。第三阶段已实现可选 Provider 调用与建议工作流，仍未配置真实模型密钥，详见 [Phase 3](phase3.md)。

- 全部后端测试 **85 passed**；Ruff、前端格式、TypeScript 和生产构建通过。JUnit 位于本地 `data/phase4-tests.xml`，仍有 1 条既有 Starlette/httpx 测试集成弃用提示。
- 第一阶段回归：CLI 两次各 22 PASSED；Web 22 PASSED；预期诊断 1 FAILED + 1 ERROR；文件插件、重启后的调度、用例创建／复制／删除、深浅主题与两种宽度通过，记录于 `data/phase4-regression-core/summary.json`。
- 第二阶段回归：CLI 两次各 9 PASSED；Web 9 PASSED；数据库、TCP 和基础性能执行正常，记录于 `data/phase4-regression-phase2/summary.json`。
- 第四阶段协议与可靠性：CLI 两次各 5 PASSED；Web 5 PASSED，0 页面错误；验证真实 protobuf、RPC 错误码与 deadline、三阶段负载、2 次 503 后连续恢复和 JUnit 导出，记录于 `data/phase4/summary.json`。
- Android：Windows 主机上的 Android 15 / API 35 x86_64 模拟器，UiAutomator2 8.7.0，实际编译、签名并安装 Java fixture，输入→点击→大写结果断言→截图，1 PASSED。报告 `data/device-android/device-report.json`，运行 `bbcb17b156804b1090cf07f3e669bf4c`。
- Windows：Windows 11、Appium 3.8.0、WinCore 3.2.0，真实启动 WinForms fixture 并执行相同交互与截图，1 PASSED。报告 `data/device-windows/device-report.json`，运行 `92c0fc72117848fe999f46a8a7f4c0a0`。
- 第三方包：安装独立 `omnitest-json-document` 包，通过 entry point 发现并读取真实 JSON；同时检查插件契约、加载错误和重复 id 隔离。

本次安装 JDK、Android SDK 与系统镜像、Appium 和两种驱动，位于忽略的 `data/tooling`。WHPX 检查可用，没有修改固件、系统可选功能或重启。Android 用例运行在实际启动的模拟器内，不代表实体手机验收。

iOS / macOS 缺少 Apple 主机与设备，只有连接协议实现，未完成平台验证。Docker、流式 gRPC、大规模负载、系统级故障注入仍未验证或实现。安装与复现步骤见 [Phase 4](phase4.md)。生成的数据库、APK、SDK、日志、截图、管理员配置和调试工具均不提交到 Git。
