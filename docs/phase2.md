# Phase 2：测试领域扩展

首个增量已经实现数据库、TCP 协议和小规模 HTTP 性能测试；没有修改 `Runner`、Test IR、任务状态机或报告生命周期。三个适配器都通过 `omnitest.adapters` entry point 接入，与第一阶段使用相同的用例、套件、CLI、API 和结果中心。

v0.4.0 新增真实 MQTT／IoT 协议执行、Cron／时区日历、重叠和补跑策略，见 [MQTT 与调度](mqtt-scheduling.md)。PostgreSQL／MySQL、更完整的环境覆盖和外部数据集导入仍为后续扩展。

## 可复现示例

重新安装项目以更新 entry point：

```bash
python -m pip install -e '.[dev]'
python -m uvicorn omnitest.demo_app:app --host 127.0.0.1 --port 8765
```

另一个终端运行 TCP fixture：

```bash
python examples/tcp_fixture.py --port 8766
```

再在平台尚未运行时创建测试定义和 fixture：

```bash
omnitest seed-phase2
omnitest run --project PROJECT_ID --suite SUITE_ID
omnitest serve
```

seed 命令输出 project_id、suite_id，在管理员 database_root 下创建示例数据库，并注册明确授权的本地目标。已有项目不会被重复创建，也不会覆盖已存在的数据库。示例含 8 个定义，参数化展开为 9 个结果：5 个数据库结果、3 个 TCP 结果、1 个性能结果。

Web 中选择 **OmniTest Phase 2** 项目，执行 **Phase 2 · adapter acceptance** 套件。运行报告包含查询行、网络响应与逐请求延迟，不使用模拟统计。

一键验收：

```bash
python scripts/acceptance_phase2.py
```

脚本自动启动并清理 HTTP、TCP、平台服务；通过 CLI 连续两次运行套件，再通过 Web 运行一次，保存三种执行器的真实结果和截图。默认端口分别为 8775、8776、8010，可通过命令行参数调整；数据保存到 `data/phase2`，不会上传 Git。

## Database Adapter

当前支持 SQLite，只读运行。管理员可在 `OMNITEST_CONFIG` 设置 `database_root`；默认是当前 data_dir 下的 fixtures，不能通过目标配置读取平台数据库或根目录外的文件。

目标：

```json
{"name":"Catalog","kind":"database","authorized":true,"config":{"path":"catalog.sqlite3"}}
```

步骤：

```json
{
  "action":"query",
  "config":{
    "sql":"SELECT name FROM products WHERE id=:id",
    "parameters":{"id":2},
    "max_rows":100,
    "query_timeout":5
  },
  "assertions":[{"path":"$.scalar","op":"eq","expected":"banana"}]
}
```

输出 `columns`、`rows`、`row_count`、`truncated`、`scalar`。SQL 参数使用数据库绑定，不拼接字符串；`scalar` 为第一行第一列，BLOB 使用十六进制表示。row_count 是返回行数，超出 max_rows 时 truncated=true，不能将其当作查询总行数。

只读 URI 加 SQLite authorizer 禁止写操作、ATTACH、PRAGMA 和 load_extension。查询在线程中运行，progress handler 限制执行时间，取消时 interrupt 并等待查询结束后清理连接。最多返回 1000 行、2 MB 结果，SQLite 单值长度上限 1 MB。

## TCP Adapter

目标 config 为 `host`、`port`、可选 `tls`；同样受管理员远程主机白名单和目标 authorized 限制。TLS 使用系统信任证书并校验主机名，没有忽略证书错误的配置。

```json
{
  "action":"exchange",
  "config":{"text":"hello\n","read_mode":"line","timeout":5},
  "assertions":[{"path":"$.text","op":"eq","expected":"hello\n"}]
}
```

也可提供 hex 代替 text。read_mode=line 等待 LF（响应包含分隔符），exact 配合 read_bytes 等待固定长度。单次收发上限 64 KB，同一用例的多个步骤共享连接，用例结束、超时或取消后关闭连接。输出 `text`、`hex`、`bytes_sent`、`bytes_received`。

它验证指定端点的协议交互，不进行端口扫描，MQTT 与 gRPC 由独立适配器实现。

## HTTP Performance Adapter

仅对目标 config 显式设置 `allow_performance: true` 时可运行。这是额外的性能测试授权，与普通目标 authorized、HTTP 写请求授权独立。

```json
{
  "action":"load",
  "config":{"path":"/health","requests":10,"concurrency":2,"rate_per_second":5,"request_timeout":5,"expected_status":200},
  "assertions":[
    {"path":"$.error_rate","op":"eq","expected":0},
    {"path":"$.latency_ms.p95","op":"lt","expected":1000}
  ]
}
```

仅支持同源 GET，不跟随重定向。单个步骤最多 100 个请求、4 个并发、20 次每秒，每个响应最多 2 MB。这些是每个工作负载的上限；多个测试用例并发运行时总网络请求数会叠加。

每个请求保存 status_code、elapsed_ms、success 和 error。汇总输出请求数、成功／失败数、error_rate、requests_per_second、latency_ms（min／mean／p50／p95／max）和状态码分布。分位数使用 nearest-rank；延迟包含请求到响应 Body 读取完成的时间，不包含排队和限速等待；吞吐量使用整个工作负载墙钟时间。

网络故障和非预期状态码作为失败样本计入错误率，再由确定性断言判断用例。配置错误是 ERROR，阈值不满足是 FAILED。这里的性能指标用于低负载回归基线，不是大规模压力测试或专业压测工具的替代品。

## 验收边界

新增测试验证参数绑定、只读和文件范围、数据库执行超时和取消、TCP 文本／二进制与超时清理、性能额外授权、限速、逐请求样本和分位数。第一阶段用例也必须继续通过。

2026-10-24 本地验收结果：完整回归 **57 passed**；第二阶段套件通过 CLI 连续运行两次，每次 **9 PASSED**，再通过 Web 运行得到 **9 PASSED**；浏览器脚本错误为 0。原始摘要为 `data/phase2/summary.json`，完整回归 JUnit 为 `data/phase2/tests.xml`。这些运行数据不会包含在 Git 源码中。

MQTT／IoT 与调度重叠、时区日历已在 v0.4.0 实现；后续继续扩展环境按目标覆盖和 CSV／JSON 数据集管理。没有设备或 broker 的功能不会用占位结果冒充完成。
