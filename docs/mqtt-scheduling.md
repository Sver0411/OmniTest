# MQTT / IoT 与日历调度（v0.4.0）

补齐第二阶段的 MQTT 协议执行及日历调度。MQTT 通过 entry point 注册，沿用 Test IR、Runner、断言、结果和远程 worker；日历沿用现有资源表和单协调服务持久化队列，无需第二个调度数据库或独立调度进程。AI 关闭时全部功能正常。

## MQTT 执行器

基于 Eclipse Paho 2.x，当前协议为 MQTT 3.1.1，提供 subscribe / publish / receive，支持 QoS 0、QoS 1、文本、JSON、二进制、保留消息和 TLS。没有实现 MQTT 5、QoS 2、WebSocket、持久会话、设备管理或嵌入式固件刷写。

目标示例：

```json
{
  "name": "Dedicated test broker",
  "kind": "mqtt",
  "authorized": true,
  "config": {
    "host": "127.0.0.1", "port": 18883,
    "topic_prefix": "omnitest/team-a/",
    "allow_publish": true,
    "allow_retained": false,
    "allow_wildcards": false
  }
}
```

主机仍受执行机器管理员白名单约束。topic_prefix 必须是以 `/` 结尾的字面前缀，不能包含 `+`、`#` 或 NUL，也不能以 `$SYS/` 开头。订阅、发布和收到的消息都必须落在此范围。通配订阅需显式 `allow_wildcards: true`；发布不接受通配符。发布需要 `allow_publish`，保留写入另需 `allow_retained`。这些是客户端执行边界，不替代 broker 的服务端 ACL。

认证可设置 `username`（普通字符串或凭据引用）和 `password_secret: "OMNITEST_SECRET_MQTT_PASSWORD"`。密码只从执行机器环境获取，受项目凭据范围约束；远程 worker 要求协调服务和节点的双侧 `project_secrets` 授权。目标不支持直接写入明文密码。

`tls: true` 使用系统信任根并校验 broker 主机名，不提供关闭证书校验的开关。本次真实认证测试使用 loopback broker，未声明已完成外部 TLS broker 的证书链验收。

步骤示例：

```json
[
  {"action":"subscribe","config":{"topic":"omnitest/team-a/telemetry","qos":1}},
  {"action":"publish","config":{"topic":"omnitest/team-a/telemetry","qos":1,"json":{"temperature":23}}},
  {"action":"receive","config":{"timeout":5},"assertions":[{"path":"$.json.temperature","op":"eq","expected":23}]}
]
```

subscribe 等待 SUBACK；publish 等待客户端发送完成（QoS 0）或 PUBACK（QoS 1）。`acknowledged` 不代表消费端完成业务处理；业务效果需由 receive 或其他适配器验证。receive 等待下一条已订阅消息，返回 topic、text、hex、json、bytes_received、qos 和 retained。JSON 解析失败时 json 为 null，原始字节仍以 hex 返回。没有先订阅就 receive 会记录 ERROR。

publish 必须恰好提供 text、hex、json 中的一种；单条负载最多 64 KB，topic UTF-8 最多 512 字节，最多缓冲 100 条收到的消息，超出限制终止该用例。每个连接使用随机 client ID 与 clean session，关闭和取消时释放网络线程；不会自动重连重放消息。底层客户端解析入站包仍会分配缓冲，面对不可信 broker 还需在 broker 和操作系统限制包大小、连接和资源。

保留消息会改变 broker 状态，只应在专用授权测试主题使用。示例在检查后通过空负载 retained publish 清除状态；用例中断时管理员仍需检查并清理对应测试主题。

## 真实本地 broker 与示例套件

开发依赖包括 aMQTT，仅作为 loopback 测试 broker，不随运行环境强制安装：

```bash
python -m pip install -r requirements-dev.lock
python -m omnitest.mqtt_fixture --port 18883
omnitest seed-mqtt --port 18883
omnitest run --project PROJECT_ID --suite SUITE_ID
```

示例 3 个用例定义，参数化后为 4 个结果：两个传感器 JSON 数据集、一个二进制检查和一个保留状态检查。所有结果实际经过 broker 收发，不用内存假消息充当协议执行。

可为 fixture 设置 `--username qa --password-env OMNITEST_SECRET_MQTT_PASSWORD`。密码只从环境读取，fixture 在临时目录写入 Argon2 哈希供 broker 认证，不输出密码。

## 日历与时区

新建／编辑定时任务使用表单，可选择间隔或 Cron、套件、环境和节点，预览接下来五次计划时间。新任务默认浏览器本地时区，API 默认 UTC；使用 IANA 名称，例如 Asia/Shanghai、Europe/Berlin。计划时间只表示触发点，仍受重叠和补跑策略影响。

Cron 使用 APScheduler 3.11 的五字段格式：分、时、日、月、周。星期从周一 0 到周日 6，可使用 `mon-fri`；日与周同时指定时必须同时满足，与部分 Unix cron 的 OR 规则不同。示例 `0 9 * * mon-fri` 表示当地工作日早上九点。无十年内有效发生时间的表达式会被拒绝。

```json
{
  "name": "Weekday regression", "suite_id": "SUITE_ID",
  "schedule_type": "cron", "cron": "0 9 * * mon-fri", "interval_seconds": null,
  "timezone": "Asia/Shanghai", "node_id": null,
  "overlap": "skip", "misfire_policy": "coalesce", "misfire_grace_seconds": 60,
  "enabled": true
}
```

原间隔配置仍接受 `interval_seconds`，不必提供 schedule_type。间隔按上次处理时刻推进，合并积压而非补齐所有历史次数。`GET /api/schedules/preview?cron=0%209%20*%20*%20mon-fri&timezone=Asia%2FShanghai` 只计算预览，不创建任务。

夏令时采用明确策略：春季不存在的当地时间跳过；秋季重复的时刻按两个不同 UTC 时间分别产生一次计划，偏移会在预览中显示。下次时间始终严格大于参考时间，避免在重复时间陷入循环。需要每天稳定一次时可选择 UTC 或非夏令时时区。

## 重叠、补跑与状态

- `overlap: skip`：该调度已有 PENDING / RUNNING 任务时跳过本次；`allow` 允许重叠。新任务默认 skip，旧数据库未带策略的记录沿用 allow。
- `misfire_policy: coalesce`：停机或延迟后仅补跑一次，不逐个重放积压触发点。默认此策略，宽限期对它不生效。
- `misfire_policy: skip`：超过 misfire_grace_seconds 的触发点直接跳过，并推进至下次计划；宽限期内可正常执行。
- `skipped_count` 是跳过决策次数，不是遗漏的每个历史触发点数量。最后运行 ID、触发时刻和跳过原因由服务器维护，创建／编辑不能伪造这些统计。

执行节点的配置撤销或引用失效会停用受影响的调度并保存 last_error，其他调度继续处理。修正后保存可清除错误。修改间隔、Cron 或时区后重新计算 next_run。配置、下次时间与统计都保存在原资源表，服务重启后恢复；已领取运行的中断策略仍见 [远程执行](remote-execution.md)。

## 验证

```bash
pytest backend/tests/test_mqtt.py backend/tests/test_calendar.py -q
python scripts/acceptance_protocols.py
```

验收脚本真实启动 broker、平台和远程 worker；先通过 CLI 运行两次 4 项 MQTT 套件，再用 Chromium 表单创建每分钟日历任务，等待真实下一分钟边界触发，由远程节点得到 4 PASSED。重启平台后验证日历配置、历史和间隔自动触发。摘要、截图与两份 CLI 报告在 `data/protocols/`，全部不提交 Git。

单元／集成测试另覆盖真实 broker 密码认证、项目凭据拒绝、队列与消息限制、错误／失败区分、取消、Cron 表达式拒绝、时区、夏令时、重叠和补跑。没有物理 IoT 设备、跨机 MQTT 网络或生产 broker 验收的声明。

实现依据：[Eclipse Paho 客户端文档](https://eclipse.dev/paho/clients/python/docs/)、[aMQTT broker 配置](https://amqtt.readthedocs.io/en/latest/references/broker_config/)、[APScheduler CronTrigger](https://apscheduler.readthedocs.io/en/3.x/modules/triggers/cron.html)。
