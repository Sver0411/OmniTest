# 数据集与环境目标覆盖（v0.5.0）

项目数据集可复用导入的 CSV／JSON 行，环境可把用例中的逻辑目标映射至同项目的其他目标。配置在提交任务时展开为不可变快照，本机与远程 worker 使用同一执行计划。整个流程无需 AI。

## 数据集

在“数据集”页面选择项目，导入 UTF-8 CSV 或 JSON。预览前 10 行，确认后才保存；CSV 每列可显式选择文本、整数、数字、布尔或 JSON。默认保留文本，因此 `001` 不会自动变成 `1`。JSON 文件必须是对象数组，保留原生类型。

每个数据集 1～100 行、最多 100 列、1 MB。各行必须有相同的非空列名，不接受重复 CSV／JSON 键、非有限数字、异常嵌套或使用 `secret.` 保留前缀的列。布尔 CSV 单元格为 true／false，JSON 列按 JSON 解析；类型错误整批拒绝，不保存部分数据。

导入记录原文件名、UTF-8 内容 SHA-256、格式和列类型，不保存用户本机路径。只改名称可保留来源；修改行内容后清除原文件来源，避免继续显示已经失效的哈希。直接 JSON CRUD 不可伪造导入来源。

用例页面点击“数据集”，选择内联数据或项目数据集。选择项目数据集会替换内联数据；也可在 Test IR 中配置 `dataset_ids: ["DATASET_ID"]`，最多引用 10 个，不能和非空 datasets 同时使用。多个数据集按引用顺序拼接行，不进行笛卡尔积。整个运行最多 500 个展开结果，超过时拒绝创建运行。

资源、引用、导入、预览和导出都受项目权限约束。editor 可导入与关联，operator / viewer 可读取与导出。被用例引用的数据集不能删除。

CLI：

```bash
omnitest import-dataset --project PROJECT_ID --file matrix.csv --column-types '{"expected":"integer"}'
omnitest list datasets --project PROJECT_ID
omnitest export-dataset DATASET_ID --format json --output matrix.json
```

PowerShell 对 JSON 参数的引用规则依所用终端版本而异。也可通过界面列类型选择导入。服务运行时加 `--url http://127.0.0.1:8000`，使用当前管理凭据，避免直接访问已锁定数据库。

API：

- `POST /api/datasets/preview`：project_id、format、filename、content、可选 name / column_types，返回列、总行数和前 10 行。
- `POST /api/datasets/import`：相同请求体，验证后创建数据集。
- `/api/resources/datasets`：沿用通用资源 CRUD，body 为 name、description、rows。
- `GET /api/datasets/{id}/export?format=json|csv`：导出真实行数据。

JSON 导出保留类型。CSV 没有类型元数据，数字／布尔／嵌套值再次导入时需选择列类型；嵌套值以 JSON 文本写入。

## 环境目标覆盖

“测试环境”表单支持变量 JSON 和目标替换，不需要修改用例来切换端点。API body 例如：

```json
{
  "name": "Staging",
  "variables": {"region":"staging"},
  "target_overrides": {"LOGICAL_TARGET_ID":"STAGING_TARGET_ID"}
}
```

两端都必须是本项目已保存且类型相同的目标。替换一次，不递归沿映射链查找。实际目标的 authorized、写操作授权与执行机器管理员白名单照常生效；环境不能直接覆盖 allow_mutations、文件根目录、CLI profile 或主机白名单。被环境引用的逻辑／实际目标均不能删除。

变量覆盖顺序保持兼容：环境变量 → 用例 environment → 用例 parameters → 套件 parameters → 本次 parameters → 数据行，右侧覆盖左侧。同一个用例引用不同数据行时产生独立结果。

手动运行、CLI `--environment`、持久化调度和远程 worker 均支持。运行报告包含选定环境，以及每个结果的逻辑目标、实际目标、数据集和行号；“运行环境与参数”可查看来源。提交后的环境、用例或数据集编辑不改变该运行。

## 验证

`backend/tests/test_datasets.py` 覆盖类型、边界、来源、引用、权限与实际 HTTP 快照执行。`scripts/acceptance_completion.py` 用浏览器导入 CSV、设置整数列、关联用例并创建环境映射，再由远程 worker 执行两个真实 HTTP 结果；同时检查 CLI 原文件哈希、成员权限及离线恢复。数据保存在 `data/completion/`，不进入 Git。
