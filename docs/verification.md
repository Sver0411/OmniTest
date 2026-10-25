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

## 第五阶段 v0.2.0 验证

- 后端完整回归 **107 passed**；新增角色与项目范围、测试凭据范围、令牌命名空间、审计、归档校验、SDK 版本拒绝及旧数据库迁移测试。记录 `data/phase5-tests.xml`，仍有 1 条既有 Starlette/httpx 测试集成弃用提示。
- 第一阶段：两次 CLI 各 22 PASSED、Web 22 PASSED；预期诊断 1 FAILED + 1 ERROR；调度重启、文件插件、资源管理和主题正常，见 `data/phase5-regression-core/summary.json`。
- 第二阶段：两次 CLI 各 9 PASSED、Web 9 PASSED，见 `data/phase5-regression-phase2/summary.json`。
- 第四阶段：两次 CLI 各 5 PASSED、Web 5 PASSED，见 `data/phase5-regression-phase4/summary.json`。
- 共享访问演练：生成独立身份，检查只读／执行／管理员界面、项目和截图隔离、请求审计；API 与 Web 各执行 2 项（HTTP + 实际浏览器截图）并通过。离线备份恢复后复查历史与 PNG 证据，见 `data/phase5/summary.json`。
- wheel 独立安装：新建虚拟环境，在源码目录之外加载包内 UI、迁移数据库、真实执行 1 项 HTTP 测试，安装独立 JSON SDK 插件并检查发现与协议声明；全部通过，见 `data/phase5/wheel-summary.json`。
- 当前本地工作台升级前已归档原数据库与证据；v0.2.0 `/api/ready` 返回 ready，已有历史保留。本机仍为原有本地管理员模式，没有自动启用共享身份或向用户暴露临时演练令牌。

新的 CI 同时覆盖 Ubuntu 与 Windows，并构建、验证和上传 wheel。这里的本地结果不代表分布式执行、SSO、OS 沙箱或新增 Apple 设备验收；这些能力仍在后续范围。

## 2026-10-25 远程执行节点 v0.3.0

- 后端完整回归 **129 passed**，JUnit 在 `data/nodes-tests.xml`，仍有 1 条既有 Starlette/httpx 测试集成弃用提示；Ruff、前端格式、TypeScript 与生产构建通过。新增并发领取、凭据隔离、节点范围、租约过期、重复结果、证据路径／校验／额度、节点撤销、调度隔离、双侧测试凭据授权及迁移／重启测试。
- 独立节点端到端：一个协调服务、一个独立被测服务与两个 worker 进程，使用不同令牌和目录。alpha 实际执行 HTTP + Chromium 截图 **2 PASSED**，beta 执行只有自身文件根目录提供的文件检查 **1 PASSED**。CLI 选择远程节点 **1 PASSED**，通过 Web 选择节点再运行 **2 PASSED**；0 页面脚本错误。摘要 `data/nodes/summary.json`，截图 `execution.png` / `operations.png`。
- 节点本地测试凭据成功执行并脱敏；未授权引用预期 ERROR。取消预期 CANCELLED。终止 worker 后平台等待 30 秒租约过期，记录 ERROR 且不重放；重新连接可执行新任务。协调服务重启使已领取任务预期 ERROR，既有通过记录与 PNG 保留，重启后新任务 PASSED。
- 本机核心回归：两次 CLI 各 **22 PASSED**，Web **22 PASSED**；预期诊断 1 FAILED + 1 ERROR，调度重启、文件插件、用例创建／复制／删除、深浅主题和 1440 / 820 宽度通过，见 `data/nodes-regression-core/summary.json`。
- 权限与恢复回归：角色界面、项目与证据隔离、身份切换、审计，以及离线恢复后历史与 PNG 检查通过，API / Web 各 **2 PASSED**，见 `data/nodes-regression-access/summary.json`。
- wheel：构建 `omnitest-0.3.0-py3-none-any.whl`，全新虚拟环境在源码目录外安装，包内界面、迁移、实际 HTTP **1 PASSED** 和独立 SDK 插件通过，见 `data/nodes/wheel-summary.json`。

本地两节点验证发生在同一 Windows 主机的独立进程中，不代表跨物理机网络、HTTPS 代理证书、SSO、多主协调或 Apple 设备实测。CI 同时为 Ubuntu / Windows 配置上述节点脚本及现有各阶段验收，远程结果以对应提交的 Actions 记录为准。

## 2026-10-25 MQTT 与日历调度 v0.4.0

- 完整后端回归 **159 passed**，JUnit 为 `data/protocols-tests.xml`，仍有 1 条既有 Starlette/httpx 测试集成弃用提示。Ruff、前端格式、TypeScript 与生产构建通过。新增 30 项协议与日历测试，覆盖真实 broker 的 QoS 0／1、JSON／二进制／保留消息、密码认证和项目凭据拒绝、真实入站消息及队列限额、取消、Cron／时区／夏令时、重叠、补跑与旧间隔配置兼容。
- MQTT 套件由 CLI 连续两次执行，每次 **4 PASSED**。Chromium 表单创建每分钟 Cron 后，等待真实分钟边界，计划时间 `1792900200.0`、实际建任务时间 `1792900200.1437333`，由独立远程 worker 执行 **4 PASSED**。重启后持久化间隔任务再次自动触发 **4 PASSED**，日历配置、历史及 JUnit 导出正常，0 页面脚本错误。摘要 `data/protocols/summary.json`。
- 本机核心回归：两次 CLI 各 **22 PASSED**，Web **22 PASSED**；预期诊断 1 FAILED + 1 ERROR；调度重启、文件插件、用例 CRUD、主题和两种宽度正常，见 `data/protocols-regression-core/summary.json`。
- 独立安装包：构建 `omnitest-0.4.0-py3-none-any.whl`，在新虚拟环境和源码外验证包内 UI、迁移、实际 HTTP 执行与 SDK 插件，见 `data/protocols/wheel-summary.json`。
- 本地工作台升级前归档 13 个数据库／证据文件，备份 SHA-256 `7f92d4b86509e14bdaa3ae11b478abfd496933799e67b53aff860fc9f32d6e67`。原 12 个运行保留，新增 MQTT 冒烟 **4 PASSED**，新增日历示例默认停用。实际服务版本 0.4.0，就绪检查通过。最新表单在 1440 / 820 宽度下无横向溢出，保存按钮可见，0 页面错误。

本批结果为 Windows 本地实测，新的远程 CI 未触发；流程已加入 MQTT、日历和远程 worker 验收。没有物理 IoT 设备、生产 TLS broker、Apple 设备或跨物理机器验收的声明。
