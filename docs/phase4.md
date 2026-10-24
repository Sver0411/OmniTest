# 第四阶段：协议、设备与故障恢复

第四阶段沿用 Test IR、Runner、任务编排和统一报告。新增能力均通过 `omnitest.adapters` entry point 注册，不需要为测试领域增加 Runner 分支，也不需要 AI Provider。

## 实现与验证范围

| 能力 | 已实现 | 本次真实验证 |
| --- | --- | --- |
| gRPC | protobuf 描述文件、unary RPC、文本 metadata、TLS、方法范围、deadline、取消 | 本地 gRPC 服务：正常响应、INVALID_ARGUMENT、DEADLINE_EXCEEDED |
| Android | Appium 定位、输入、点击、状态检查、PNG 证据、会话清理 | Android 15 / API 35 模拟器上的原生 Java 应用 |
| Windows | 相同 Appium 执行协议，管理员限定应用或窗口 | Windows 11 + WinCore 驱动上的原生 WinForms 应用 |
| iOS / macOS | Appium 连接入口和相同基础步骤 | 未验证；需要独立 Apple 主机、Xcode、相应驱动与设备或模拟器 |
| 分阶段负载 | 依次运行不同请求数、并发数与速率的阶段，分别统计延迟和错误率 | 本地 HTTP 服务真实工作负载 |
| 故障恢复 | 管理员 HTTP 故障 profile、恢复操作、连续采样、恢复时间和可用率 | 注入 2 次 HTTP 503 后恢复；取消后自动撤销故障 |
| 独立第三方插件 | 包安装、entry point 发现、声明与异步契约检查、重复和损坏插件隔离 | 安装独立 JSON 文件插件并实际读取文件 |

设备验证与协议 fixture 测试分开记录。pytest 的 WebDriver fixture 只验证客户端协议与清理行为，不能证明 iOS / macOS 设备可用。Android 和 Windows 验证使用实际 Appium 驱动、实际应用和统一执行报告。

## gRPC

开发锁文件已包含依赖。仅安装运行包时使用 `pip install -e ".[grpc]"`；未安装可选依赖仍可启动平台，执行 gRPC 时返回明确安装提示。

管理员配置 `grpc_descriptor_root`，默认 `<data_dir>/descriptors`。将含全部 imports 的 FileDescriptorSet 放在此目录。自己的 `.proto` 可使用 protoc 的 `--descriptor_set_out=service.pb --include_imports` 生成；平台不会执行上传的 Python 代码，也不依赖服务开启 reflection。

目标配置：

```json
{
  "name": "Catalog RPC",
  "kind": "grpc",
  "authorized": true,
  "config": {
    "host": "127.0.0.1",
    "port": 8786,
    "tls": false,
    "descriptor_set": "echo.pb",
    "allow_rpc": true,
    "allowed_methods": ["/omnitest.fixture.EchoService/Echo"]
  }
}
```

步骤：

```json
{
  "action": "unary",
  "config": {
    "method": "/omnitest.fixture.EchoService/Echo",
    "request": {"message": "hello"},
    "metadata": {},
    "timeout": 5
  },
  "assertions": [
    {"path": "$.status", "op": "eq", "expected": "OK"},
    {"path": "$.response.message", "op": "eq", "expected": "hello"}
  ]
}
```

`request` 和 `response` 使用 protobuf JSON 映射，响应保留 proto 字段名。RPC 错误作为 `status` / `details` 观察值返回，须通过断言检查预期状态。客户端发送与接收各限 1 MB，描述文件限 2 MB，单次 RPC 最长 30 秒。远程主机仍需管理员白名单；TLS 使用系统信任链。当前不支持流式 RPC、binary metadata、自定义 CA 和双向 TLS。

## 分阶段负载

目标仍是 `http`，要求 `config.allow_performance: true`。`load` 保持兼容，新增 `stages`：

```json
{
  "action": "stages",
  "config": {"stages": [
    {"path": "/health", "requests": 10, "concurrency": 1, "rate_per_second": 5},
    {"path": "/health", "requests": 20, "concurrency": 2, "rate_per_second": 10},
    {"path": "/health", "requests": 30, "concurrency": 4, "rate_per_second": 20}
  ]},
  "assertions": [{"path": "$.error_rate", "op": "eq", "expected": 0}]
}
```

最多 5 阶段，每阶段最多 100 请求、4 并发、20 请求/秒。阶段间也维持请求间隔，先校验全部阶段才开始发送。每阶段提供原始 samples、开始时间、状态码、失败数、吞吐及 min/mean/p50/p95/max；顶层提供总请求数与错误率。限额按执行实例计算，多用例并发会叠加负载。仅同源 GET，不自动跳转；属于有界负载测试，尚未提供分布式压测、长时间 soak 或开放式到达率模型。

## 故障与恢复

目标需 `allow_reliability: true`。`observe` 按固定间隔 GET，返回真实 `timeline`、`availability`、`failure_observed`、`stable`、`recovered` 和 `recovery_seconds`。健康基线不会被标为“故障恢复”；恢复要求观察到失败后连续成功达到 `stable_samples`，后续再次失败会撤销恢复标记。

可选故障注入只引用管理员配置，不能提交 shell、进程名、任意 URL 或故障请求体。`OMNITEST_CONFIG` 示例：

```json
{
  "http_faults": {
    "local-recovery": {
      "url": "http://127.0.0.1:8785/faults/recovery",
      "body": {"failures": 2},
      "restore_url": "http://127.0.0.1:8785/faults/recovery",
      "restore_body": {"failures": 0}
    }
  }
}
```

profile 可配置 `headers`，其中敏感值使用 `${secret.OMNITEST_SECRET_NAME}`。目标还需 `allow_faults: true` 和 `fault_profiles: ["local-recovery"]`，三方授权同时满足才允许执行 `fault`。每个实例只允许一个活动故障；`restore` 和 `close` 都会请求恢复。每次控制请求总超时 3 秒；恢复失败会记录清理错误，无法保证外部控制服务失联时恢复成功。生产故障控制服务应自行提供到期撤销能力。

典型步骤顺序：

```json
[
  {"action":"fault","config":{"profile":"local-recovery"}},
  {"action":"observe","config":{"path":"/service/recovery","samples":5,"interval_seconds":0.1,"stable_samples":3},
   "assertions":[{"path":"$.recovered","op":"eq","expected":true}]},
  {"action":"restore"}
]
```

目前提供 HTTP 控制接口与 HTTP 健康观测，尚未提供系统级网络分区、进程终止、Kubernetes chaos 或数据库故障注入。

## Appium 与本机设备环境

当前安装目录为 `data/tooling`，不写入 Git。环境包括 Temurin JDK 17、Android SDK / API 35 / Build Tools 35、Android Emulator、Appium 3.8.0、UiAutomator2 8.7.0 和 WinCore 3.2.0。仅在当前 PowerShell 会话设置环境变量，不修改系统 PATH。

新 Windows 环境可运行以下脚本，下载来源为官方 Android / Adoptium 及 npm 包；JDK 和 Android 命令行工具下载后检查 SHA-256：

```powershell
# Review the Android SDK license before using this switch.
.\scripts\setup_devices.ps1 -AcceptAndroidLicenses
. .\scripts\device_environment.ps1
& "$env:ANDROID_HOME/emulator/emulator.exe" -accel-check
```

脚本不会修改固件、启用系统功能或自动重启。如果 WHPX 不可用，需先在主机启用对应虚拟化支持。本次检测 WHPX 可用，无需此类系统修改。首次创建并启动项目专用模拟器：

```powershell
'no' | & "$env:ANDROID_HOME/cmdline-tools/latest/bin/avdmanager.bat" create avd -n OmniTest -k 'system-images;android-35;google_apis;x86_64' -p "$env:ANDROID_AVD_HOME/OmniTest.avd" --device pixel_6
& "$env:ANDROID_HOME/emulator/emulator.exe" -avd OmniTest -no-window -no-audio -no-boot-anim -gpu software -no-snapshot -port 5554 -no-metrics
```

另一个终端加载相同环境，等待 `adb -s emulator-5554 shell getprop sys.boot_completed` 返回 `1`，并确认 package manager 可用。第一次启动可能在 boot 标志后继续初始化，不应立即开始安装应用。

```powershell
. .\scripts\device_environment.ps1
python scripts/build_android_fixture.py
adb -s emulator-5554 install -r data/android-build/fixture.apk
& data/tooling/appium/node_modules/.bin/appium.cmd --address 127.0.0.1 --port 4725
```

Appium 服务须继承 `JAVA_HOME`、`ANDROID_HOME`、`APPIUM_HOME`。不要为该服务启用 relaxed security 或任意脚本功能。

管理员 JSON 增加 profile，下面的应用路径应替换为本机绝对路径：

```json
{
  "appium_profiles": {
    "local-android": {
      "server_url": "http://127.0.0.1:4725",
      "capabilities": {
        "platformName": "Android",
        "appium:automationName": "UiAutomator2",
        "appium:udid": "emulator-5554",
        "appium:app": "D:/File/OmniTest/data/android-build/fixture.apk",
        "appium:appPackage": "org.omnitest.fixture",
        "appium:appActivity": ".MainActivity"
      }
    }
  }
}
```

测试目标是 `{"name":"Android fixture","kind":"android","authorized":true,"config":{"profile":"local-android"}}`。profile 的 URL、capabilities、应用路径和窗口只能由管理员文件设置，不能通过目标或步骤覆盖。远程 Appium 主机需管理员 `allowed_hosts`。

Appium 步骤：`fill {value,text,by?,clear?}`、`click {value,by?}`、`inspect {value,by?}`、`screenshot {}`。定位默认 `accessibility id`，也支持 id、xpath、name、class name。`inspect` 返回 text/displayed/enabled。截图存入用例证据；Appium 截图可能包含被测应用或桌面的实际内容，不提供图像内敏感信息自动遮盖。不要用包含真实凭据的页面生成可公开分享的证据。

会话创建最长 90 秒、普通命令最长 30 秒、隐式定位等待 5 秒、响应上限 8 MB。统一用例超时仍生效。取消时等待会话创建完成再删除；超出同步清理等待后继续后台清理，断网时依赖服务端 30 秒空闲会话回收。此机制不构成设备级隔离，同一设备的套件应设 `concurrency: 1`。

真实 Android 验收：

```powershell
$env:OMNITEST_CONFIG = "$PWD/data/device-admin.json"
python scripts/acceptance_devices.py --kind android --profile local-android --data-dir data/device-android
```

Windows fixture 位于 `examples/desktop_fixture.ps1`。管理员 profile 使用 `platformName: Windows`、`appium:automationName: Wincore`、`appium:app: C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe`、`appium:appArguments: -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "本机绝对路径/examples/desktop_fixture.ps1"`、`appium:shouldCloseApp: true`。这会通过管理员固定命令启动测试窗口，隐藏其控制台；用例只能操作此应用。运行同一验收脚本并指定 `--kind windows --profile local-windows`。也可手动启动脚本，通过 `-HandleFile ABSOLUTE_PATH` 获取十进制窗口句柄，在 profile 中用 `appium:appTopLevelWindow` 替换 app 和 appArguments；此模式每次重启窗口应更新句柄。

iOS 需在 Mac 主机运行 XCUITest，macOS 应用需 Mac2，分别使用 `platformName: iOS` / `mac`，OmniTest 目标类型使用 `ios` / `macos`。连接协议已实现，尚无 Apple 主机完成驱动和应用验证；不能在 Windows 上补装 Xcode 来替代。参考 [XCUITest](https://appium.github.io/appium-xcuitest-driver/) 和 [Mac2](https://appium.github.io/appium-mac2-driver/)。Windows 采用适用于 Windows 10+ 的 [WinCore](https://github.com/appium/appium-wincore-driver)，没有依赖已停止维护的旧 WinAppDriver。

## 可复现验收

```powershell
python scripts/acceptance_phase4.py
```

自动启动独立 HTTP / gRPC fixture，创建 5 个用例，两次 CLI 真实执行后再通过浏览器运行套件，验证 AI 默认关闭、持久化历史、JUnit 导出和新插件展示。默认端口 8785、8786、8011；可通过同名 `--fixture-port`、`--grpc-port`、`--platform-port` 参数调整。服务退出时清理，数据与截图留在 `data/phase4`。

独立 JSON 插件位于 `examples/plugins/json_document`，开发锁文件会安装此包；生产用户可单独 `pip install ./examples/plugins/json_document`，通过 `json-document` 执行器读取管理员 `file_root` 内的 JSON。示例包有自己的 pyproject、版本和 entry point，与 OmniTest 内置包分开安装。插件不是安全沙箱，只能由管理员安装可信包，详见 [插件文档](plugins.md)。
