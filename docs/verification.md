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
