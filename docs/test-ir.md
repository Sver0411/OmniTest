# Test IR 与统一断言

完整 Schema 由 `GET /api/settings` 中的 schemas.cases 返回，或 `TestIR.model_json_schema()` 生成。稳定字段包括 id、name、description、target、adapter、preconditions、parameters、datasets、steps、assertions、environment、timeout、retries、tags、expected_results、metadata。

```json
{
  "schema_version": 1,
  "name": "Health API",
  "description": "Validate a deterministic health endpoint",
  "target": "PROJECT_TARGET_ID",
  "adapter": "api",
  "preconditions": [],
  "parameters": {"expected": "ok"},
  "datasets": [],
  "environment": {},
  "steps": [
    {
      "id": "health",
      "action": "request",
      "config": {"method": "GET", "path": "/health"},
      "assertions": [
        {"path": "$.status_code", "op": "eq", "expected": 200},
        {"path": "$.json.status", "op": "eq", "expected": "${expected}"}
      ]
    }
  ],
  "assertions": [{"path": "$.last.duration", "op": "lt", "expected": 5}],
  "timeout": 30,
  "retries": 0,
  "tags": ["functional", "regression"],
  "expected_results": "Healthy service responds within 5 seconds",
  "metadata": {"owner": "quality"}
}
```

步骤断言的 JSONPath 从该步骤输出开始；用例级断言从 `{parameters, environment, steps, last}` 开始。steps 按唯一的步骤 id 索引；环境参数的最终解析值在 parameters 中。前置条件在任何适配器启动前执行，不满足则跳过。

参数优先级从低到高：所选环境 variables → 用例 environment → 用例 parameters → 套件 parameters → 运行 parameters → 当前 dataset。`${key}` 整串引用保留原始 JSON 类型，混合字符串引用转为字符串。不支持表达式求值、Python、Shell 或模板代码执行。`${secret.OMNITEST_SECRET_X}` 只从服务进程环境中解析。

datasets 是对象数组，每项产生一个独立的结果。id 在创建时由服务器分配，导入时会重新分配；name 与 dataset 参数共同解释结果，报告包含参数快照。

| 断言 | 语义 |
| --- | --- |
| eq / ne | 等于／不等于 |
| contains / not_contains | 文本或集合包含关系 |
| gt / gte / lt / lte | 数值／可比较值比较，可用于耗时阈值 |
| regex | 兼容 re 的文本匹配，单次匹配上限 50ms |
| exists | 路径有非 null 值；expected 为 true／false |
| set_eq | 集合比较，忽略顺序和重复项 |
| status_code / exit_code | 显式状态码／退出码等值比较 |
| visible / file_exists | 领域输出中的布尔值比较 |

JSONPath 支持 `$.json.items[0].name`、`$.json.items[*].id` 和带引号的文件名／header 键。单匹配返回该值，多匹配返回列表；除 exists 外，缺失路径会给出失败说明。每条断言保留 path、op、expected、actual、passed、error、description，能够从持久化输出复查。

API Adapter：action=request，config 支持 method、path、headers、query、json、body，输出 status_code、headers、json、text、url、duration。写请求需目标授权。

Web Adapter：goto(path)、fill(selector,value,read_value)、click(selector)、wait(selector,state)、inspect(selector,read_value)、screenshot。输出 url、title、count、visible、text、value、duration。每个用例独立 browser context；步骤执行失败或步骤断言失败留截图。

CLI Adapter：action=run，config.args 为字符串数组，config.files 为本次工作目录内需要检查／保留的相对文件名数组。输出 exit_code、stdout、stderr、duration、files。为跨系统复现，将 stdout／stderr 的 CRLF 标准化为 LF。

文件 Adapter：action=inspect，target.config.path 为管理员 fixture 根目录内的路径。输出 exists、size、sha256、text，读取上限 1 MB。
