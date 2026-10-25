# 远程执行节点（v0.3.0）

第五阶段的第二批工程化增量支持一个协调服务与多个执行节点。协调服务保存项目、队列、审计、报告和证据；每个 worker 使用独立数据目录，通过 HTTP(S) 主动领取分配给自己的任务，复用既有 Runner 和插件协议。worker 不共享 SQLite 文件，也不需要开放入站端口。

## 配置协调服务

先在协调服务创建项目、目标、用例和套件。将 [配置示例](../examples/remote-nodes.json) 的 `PROJECT_ID` 替换为实际项目 ID，合并到管理员 `OMNITEST_CONFIG` 文件。配置变更重启生效。

每个节点声明：

- `id`：1～80 位字母、数字、下划线或连字符，作为手动运行和调度的节点标识。
- `token_env`：`OMNITEST_NODE_*` 环境变量名称。实际令牌至少 32 字符，每个节点独立，不能复用管理 API 令牌。停用节点可设置 `enabled: false`。
- `project_ids`：允许接收的项目，省略为空列表，显式 `null` 表示所有项目。
- `adapters`：该节点获准执行的适配器 ID，提交时检查整批用例。worker 还会报告实际安装清单，未安装所需插件的节点不会领取任务。

启用远程节点必须同时配置管理 API 凭据，支持既有 `OMNITEST_API_TOKEN` 或命名 `access_tokens`。节点令牌只可访问 `/api/worker/*`，不能读写项目、查询其他节点或下载报告。管理 API 令牌不能用于领取任务。没有节点配置的既有本地模式继续可用。

PowerShell 示例：

```powershell
$env:OMNITEST_CONFIG = "$PWD/data/administrator.json"
# Generate separate random values with secrets.token_urlsafe(32), then set:
# OMNITEST_API_TOKEN / OMNITEST_NODE_ALPHA / OMNITEST_NODE_BETA
omnitest --data-dir data/coordinator serve --host 127.0.0.1
```

远程连接使用受控 HTTPS 入口。worker 仅允许 loopback HTTP，其他主机地址必须使用 HTTPS，正常验证证书；不会跟随重定向或使用系统代理。协调服务可由 HTTPS 反向代理转发至本机 API。

## 启动 worker

在每台节点机器安装同一版本的 OmniTest 及需要的插件、浏览器、设备驱动。`omnitest plugins` 检查安装清单。Web 测试需要在该节点安装 Playwright Chromium，CLI / Appium / 数据库 / 文件 / gRPC 的管理员 profile、文件根目录、描述文件和设备连接也在该节点配置。

```powershell
$env:OMNITEST_NODE_ALPHA = "<the separate node token>"
$env:OMNITEST_CONFIG = "$PWD/worker-settings.json"
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD/data/browsers"
omnitest --data-dir data/worker-a worker --coordinator https://tests.example.com --token-env OMNITEST_NODE_ALPHA
```

每个 worker 使用独立目录；启动时获取目录锁，不能与平台服务或另一 worker 共用。`--once` 在处理一个已领取任务后退出，等待期间仍轮询；它的退出表示进程正常处理完任务，测试结论以平台报告为准。

测试中的 localhost、本地路径、CLI 程序和设备均指执行节点自身。任务目标仍需 `authorized: true`，节点管理员白名单仍生效。协调服务的白名单和 profile 不会自动下发，需为各节点独立配置。插件须先在协调服务注册，才能创建对应的用例。

### 测试凭据

任务只传 `${secret.OMNITEST_SECRET_*}` 引用及获准名称，不下发服务环境变量。实际被测系统凭据只放在 worker 的环境中。远程执行始终要求协调服务和 worker 的 `project_secrets` 同时授权，取两者交集，默认不给任何测试凭据。

例如两侧管理员配置均包含：

```json
{"project_secrets":{"PROJECT_ID":["OMNITEST_SECRET_STAGING_KEY"]}}
```

然后仅在 worker 设置 `OMNITEST_SECRET_STAGING_KEY` 的实际值。worker 返回前对日志、断言、结果和文本证据脱敏；节点令牌使用独立前缀，也纳入脱敏。已有截图遮罩规则继续适用。

## 使用入口

Web：“测试执行”选择项目后，用“执行节点”选择本机或获准的远程节点，再执行用例或套件。离线节点可排队等待连接，用户可取消；不会自动转移至其他节点。报告及运行列表显示实际分配节点，“运行维护”显示在线状态、已安装插件、版本和最近连接时间。节点配置由管理员文件维护。

CLI：

```bash
omnitest --url https://tests.example.com run --project PROJECT_ID --suite SUITE_ID --node worker-a
```

客户端 `OMNITEST_API_TOKEN` 填入拥有项目执行权限的管理凭据。API 为既有 `POST /api/runs` 增加可选 `node_id`；`null` 为本机。持久化调度资源也增加 `node_id`，沿用现有间隔调度。节点撤销或适配器授权变化导致调度无法提交时，平台自动停用该调度并保存 `last_error`，其他调度继续执行；修正配置并重新保存后清除错误。`GET /api/nodes?project_id=...` 返回当前身份可见的节点，详细清单和其他项目范围仅向管理员提供。

## 生命周期与证据

一个节点同时领取一个运行，套件内并发取运行配置与节点 `max_concurrency` 较小值，默认最多 4 个用例。本机队列独立，远程等待任务不会堵住本机任务。多个节点可同时执行，合计并发是各节点上限之和。

每个用例完成后立即回传证据和结果，不再等待同一套件的所有用例结束。报告按原执行计划排序；慢用例或后续断联不覆盖已经接收的完成结果。界面报告的计数直接读取已回传结果，支持状态、名称、ID 和错误搜索，并标记重试后的通过。

领取时生成独立租约，节点约每 3 秒续约，30 秒未续约后任务转为 ERROR。旧租约、其他节点和已完成任务不能提交结果。已接收的单用例结果保留；断联时尚未结束的用例标为 ERROR。任务不会因断联、worker 重启或平台重启自动重放，以避免重复操作。用户可在确认被测系统状态后手动重新运行。

取消运行先设置取消请求，worker 续约收到后停止用例并回传 CANCELLED；断联时无法确认执行端是否已停止，平台按租约失联记录 ERROR。平台重启将已领取任务标为中断 ERROR，尚未领取的任务保留在队列。worker 的超时与清理沿用执行器限制；这不是强制终止远程机器上所有子进程的操作系统沙箱。

证据通过独立接口先上传，再保存单用例结果。平台检查租约、用例归属、路径、SHA-256、重复内容和额度：单文件 2 MB，每个运行最多 256 个文件、总计 64 MB；最多四次尝试，文件名须为简单 ASCII 文件名。超过限制的证据传输记录为 ERROR，不能伪装成截图已保存。成功回传后，证据由协调服务统一下载、导出和备份；worker 本地副本由管理员自行保留或清理。

任务领取和正常心跳不逐条写审计，避免空轮询淹没操作记录；结果、证据、完成请求和认证拒绝继续审计。运行报告保留管理端提交者，回传请求记录节点身份。节点是受信任执行机器，可看到分配给自己的测试定义和参数，不适合接入不受信任的第三方机器。

## 验证与边界

```bash
pytest backend/tests/test_nodes.py backend/tests/test_upgrade.py -q
python scripts/acceptance_nodes.py
```

端到端脚本实际启动协调服务、独立被测服务与两个 worker 进程，使用不同令牌和根目录。覆盖 HTTP、浏览器截图、只有第二个节点拥有的文件、节点本地凭据、未授权凭据、CLI、UI 节点选择、取消、终止 worker、平台重启、报告与 PNG 保留。结果在 `data/nodes/summary.json`，截图为 `execution.png` / `operations.png`。临时令牌只注入进程环境，不写配置或摘要。Ubuntu / Windows CI 均运行该脚本。

本次验证两个节点进程在同一主机上通过真实 HTTP 通信，证明进程、目录与任务分配链路；未声称已完成跨物理机器网络、真实代理证书或 Apple 设备验收。

当前仍是单协调服务、SQLite 和固定节点分配。没有多主协调、Redis/Celery、自动重试迁移、跨节点套件拆分、动态节点注册、租户操作系统隔离或 SSO。远程执行不依赖 AI，关闭 AI 不影响任务领取与执行。
