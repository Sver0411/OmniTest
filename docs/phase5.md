# 第五阶段：平台工程化（v0.2.0）

本次增量提供项目级凭据权限、请求审计、运行状态、离线备份恢复、公开适配器 SDK 和完整 wheel 发布包。本节记录 v0.2.0 首批工程化能力。v0.3.0 在单协调服务基础上增加固定节点远程执行，见 [远程执行节点](remote-execution.md)。多主协调、分布式队列、SSO、团队成员数据库和进程配额尚未实现。

## 共享访问与项目权限

通过管理员 `OMNITEST_CONFIG` 文件的 `access_tokens` 配置不同身份，令牌值仅放在服务进程环境变量中。示例见 `examples/access-control.json`，先将其中 PROJECT_ID 替换为实际项目 ID。配置需重启服务生效。

| 角色 | 授权项目内查看与导出 | 运行、参数化、取消任务 | 编辑用例、目标、环境、套件和调度 | 创建／删除项目、AI 管理、运维与审计 |
| --- | --- | --- | --- | --- |
| viewer | 是 | 否 | 否 | 否 |
| operator | 是 | 是 | 否 | 否 |
| editor | 是 | 是 | 是 | 否 |
| admin | 全部项目 | 是 | 是 | 是 |

editor 可以编辑所属项目名称。项目归属仍不可更改，资源间引用仍需属于同一项目。operator 可以查看项目内测试定义与环境并传入运行参数，因此应分配给可信测试成员。

非管理员的 `project_ids` 默认是空列表，即没有项目权限；显式 `null` 表示全部项目。管理员是全局身份，不支持分配非空项目范围。列表、统计、详情、执行、取消、JSON/JUnit 导出和证据下载都在后端检查范围，隐藏按钮只是界面反馈。

```json
{
  "access_tokens": [
    {"name":"maintainer","role":"admin","token_env":"OMNITEST_ACCESS_ADMIN"},
    {"name":"qa-runner","role":"operator","token_env":"OMNITEST_ACCESS_OPERATOR","project_ids":["PROJECT_ID"]},
    {"name":"qa-viewer","role":"viewer","token_env":"OMNITEST_ACCESS_VIEWER","project_ids":["PROJECT_ID"]}
  ],
  "project_secrets": {"PROJECT_ID":["OMNITEST_SECRET_STAGING_KEY"]}
}
```

生成每个身份独立的随机令牌，例如 `python -c "import secrets;print(secrets.token_urlsafe(32))"`，将结果分别设置到相应环境变量。不要把实际令牌写入 JSON、测试用例或 Git。新命名凭据至少 16 字符，不允许重复令牌或重复身份名称。缺失的启用令牌会导致启动失败；全部凭据 disabled 后依然要求认证，避免意外降级为匿名访问。

Windows PowerShell 启动示例：

```powershell
$env:OMNITEST_CONFIG = "$PWD/data/administrator.json"
# Set OMNITEST_ACCESS_ADMIN / OPERATOR / VIEWER in this service process environment.
omnitest --data-dir data serve --host 0.0.0.0
```

UI 在“系统设置”保存当前身份的访问令牌到 sessionStorage。切换凭据会清除界面中的项目、列表、报告与编辑状态，再加载新范围。只读身份禁用修改和执行按钮，AI 与运行维护页面只对管理员显示。

旧 `OMNITEST_API_TOKEN` 保持为全局管理员令牌。采用命名凭据时建议不再设置它；同一令牌不能同时作为旧管理员令牌和命名凭据使用。没有任何认证配置时仍是本地管理员模式，非 loopback 绑定必须配置认证。共享部署应使用 HTTPS 或受控的本地代理。

CLI 的 `--url` 模式仍从客户端 `OMNITEST_API_TOKEN` 读取 Bearer 值，可以填入 operator / viewer 的实际令牌；这不会改变服务端角色。本地 CLI 直接访问数据库，属于文件系统管理员工具，不受 HTTP 身份权限限制。

### 测试凭据与访问令牌分离

平台访问令牌使用 `OMNITEST_ACCESS_*`，Test IR 不能通过 `${secret...}` 读取它们。被测系统凭据继续使用 `OMNITEST_SECRET_*`。

配置命名身份后，所有执行入口（API、CLI、持久化调度）只允许读取管理员分配给该项目的 `project_secrets` 名称，默认不分配任何测试凭据。此约束也适用于管理员发起的运行；需要使用测试凭据时应显式配置项目映射。未授权的引用在执行时记录 ERROR，值不会被解析。未配置命名身份的原有单管理员模式保持兼容。

管理员预先定义的 CLI / Appium / 故障控制 profile 仍属于受信任服务器配置，插件是受信任 Python 代码。项目权限约束的是管理 API 与测试凭据，不是操作系统沙箱，也不代表多个互不信任租户的设备或文件系统隔离。

## 审计与运行状态

- `GET /api/session`：当前身份、角色、项目范围和界面权限。
- `GET /api/health`：公开存活信息，不需要令牌。
- `GET /api/ready`：公开检查数据库连接与调度循环；不可用返回 503，不检查具体被测设备或业务目标。
- `GET /api/operations`：管理员查看版本、活动任务数、状态计数、最早排队时间、并发上限、审计数量和插件错误。
- `GET /api/audit?limit=50&project_id=...`：管理员查看请求审计，最多返回 200 条。

请求审计覆盖 API 写请求及 401/403 拒绝，保存操作者、角色、方法、路径、项目、HTTP 状态、耗时和服务器生成的请求 ID。所有响应返回 `X-Request-ID`，手动运行报告另有 `submitted_by`。

审计不保存请求体、请求头、查询参数或令牌。它是请求级记录，不是资源修改前后差异，也不是不可篡改日志；持有数据库文件权限的管理员可以更改数据。审计写入独立于业务事务，持久化失败会记录服务器错误日志，不把已经完成的业务操作伪装为失败并诱发重复提交。当前不提供审计保留期限清理或外部日志存储。

“运行维护”页面展示真实运行数据和最近 50 条操作。可用于排队和故障排查，不包含虚构的 CPU、设备在线率或分布式节点状态。

## 备份与恢复

先停止使用同一数据目录的服务。CLI 会取得原有 worker lock，运行中的服务持锁时拒绝备份，确保数据库与证据文件处于同一离线窗口。

```powershell
omnitest --data-dir data/release backup --output data/backups/release.zip
omnitest --data-dir data/restored-release restore --archive data/backups/release.zip
omnitest --data-dir data/restored-release serve
```

恢复目标必须不存在，不覆盖现有数据。备份使用 SQLite backup API 生成快照，包含 WAL 中已提交数据；不是直接复制正在使用的 db 文件。

归档内容：`omnitest.db`、数据目录内的 `evidence/`、`fixtures/`、`descriptors/` 和带每文件大小、SHA-256 的 manifest。恢复前检查名称规范、目录范围、重复项、符号链接、文件哈希、SQLite integrity_check 和外键；全部通过后才安装新目录。上限为 50,000 文件、2 GB 未压缩数据。

管理员配置、环境变量、模型密钥、SDK、浏览器、APK、外部自定义根目录和任意其他数据目录内容均不包含在归档中。恢复后须重新提供管理员配置、环境变量和执行依赖；管理员配置也可能引用原有外部路径，应按新主机位置调整。归档包含项目、运行快照和证据等实际数据，不提供加密或脱敏副本。

## 公共插件 SDK 与版本

插件应从稳定入口导入通用接口：

```python
from omnitest.sdk import (
    ADAPTER_API_VERSION,
    AdapterInfo,
    ExecutionContext,
    Model,
    Step,
    validate_adapter,
)
```

当前 `ADAPTER_API_VERSION = 1`。`AdapterInfo.api_version` 默认 1，旧构造方式保持兼容。注册表先验证声明、标识、能力与 async 生命周期，拒绝其他 API 版本；不会为了验证而启动浏览器、连接设备或执行构造器。错误插件不会覆盖正常插件。

```powershell
omnitest plugins
omnitest --url http://127.0.0.1:8000 plugins
```

输出区分执行器版本、接口版本和实际安装的 distribution 名称／版本。界面适配器卡片也展示安装包版本。独立示例 `omnitest-json-document 0.2.0` 已改用 SDK，包依赖 `omnitest>=0.2,<1`。版本策略是管理员安装、约束依赖并重启加载，尚无网页安装任意包、自动升级或回滚功能。

## 完整发布包

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
$env:OMNITEST_REQUIRE_UI = '1'
python -m build --wheel
python scripts/verify_wheel.py dist/omnitest-0.3.0-py3-none-any.whl
```

构建时将已生成的 SPA 放入 wheel，包含后端、迁移、SDK、插件入口及界面资源，排除开发测试包。`OMNITEST_REQUIRE_UI=1` 可防止发布缺少界面的包。源码开发仍优先使用项目的 `frontend/dist`，安装后的服务在源码目录之外使用包内静态资源。

```powershell
python -m pip install "dist/omnitest-0.3.0-py3-none-any.whl[grpc]"
omnitest --data-dir PATH_TO_DATA init
omnitest --data-dir PATH_TO_DATA serve
```

没有前端构建环境时仍可通过 `pip install -e .` 安装后端开发接口，但正式发布构建应先生成 SPA。Playwright 浏览器、Appium 服务与驱动、被测应用和管理员 profile 仍需单独准备。仓库中的 seed-demo / 原生 fixture 源码面向源码开发工作流，不会把整套 SDK、示例运行环境和机器配置打入平台 wheel。

`verify_wheel.py` 新建独立虚拟环境，按开发锁文件约束安装运行依赖，然后在临时目录验证包路径、静态资源、数据库初始化、真实 HTTP 执行、审计和独立 SDK 插件，避免 editable 安装掩盖缺失文件。

GitHub Actions 扩展为 Ubuntu / Windows 两个平台，执行回归、四个阶段的端到端验收、wheel 构建和独立安装检查，上传各平台验证记录及 wheel。没有自动发布到 PyPI、部署服务器或创建公开版本标签。

Linux CI 另行构建 Docker 镜像并运行 `scripts/verify_container.py`：检查包内 UI、认证写请求、真实 HTTP 用例、审计及容器重启后的历史持久化。镜像采用前端／wheel 构建阶段和独立运行阶段，仅在运行层安装受约束的运行依赖、gRPC 与 Chromium，使用普通用户和 `/api/ready` 健康检查。Windows 主机没有安装 Docker，容器运行结果以对应 Linux CI 记录为准。

## 可复现验收

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD/data/browsers"
python scripts/acceptance_phase5.py
```

脚本生成临时命名凭据，通过实际 HTTP 服务验证项目范围、角色、运行发起人、审计和证据隔离；用 Chromium 检查只读按钮、切换身份、执行套件与运维界面；停止服务后归档并恢复到新目录，再用相同外部身份配置检查历史与 PNG 证据。默认端口 8015 / 8798，可通过 `--platform-port` / `--fixture-port` 修改。

每次 API 套件和浏览器套件各执行 2 个实际用例（HTTP + Web 截图），结果保存在 `data/phase5/summary.json`，界面截图 `viewer.png` / `operations.png`。生成的令牌不会写入摘要、配置或 Git，也不能在脚本退出后作为常设身份使用。
