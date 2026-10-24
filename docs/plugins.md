# 适配器与断言插件开发

适配器是管理员安装的可信 Python 包，不能由测试用例上传源码或指定任意导入路径。

接口定义位于 `omnitest.adapters.base`：

```python
from omnitest.adapters.base import AdapterInfo

class ExampleAdapter:
    info = AdapterInfo(
        id="example",
        version="1.0",
        capabilities=["inspect"],
        requirements=["domain runtime"],
        target_kinds=["example-target"],
    )

    async def check(self, context):
        # Validate target configuration and acquire domain resources.
        self.target = context.target

    async def execute(self, step, context):
        # Implement real domain operations and return JSON-serializable observations.
        if step.action != "inspect":
            raise ValueError("Unsupported action")
        return {"name": self.target["name"]}

    async def close(self):
        # Release acquired resources, including partial initialization.
        pass
```

注册 entry point：

```toml
[project.entry-points."omnitest.adapters"]
example = "your_package.adapter:ExampleAdapter"
```

安装包并重启服务后，注册表自动发现，前端适配器页面和 /api/adapters 展示声明。重复 id 拒绝覆盖；加载错误返回 errors，其他插件仍可运行。每个用例尝试使用独立实例，check／execute 受统一用例超时控制，close 无论成功或失败都会调用。适配器必须正确传播 CancelledError，避免执行阻塞事件循环的无限操作。

`ExecutionContext` 包含管理员 settings、目标快照 target、合并变量 variables、独立 evidence_dir、logs 与 evidence 列表。证据文件写入 evidence_dir 后，将相对文件名追加到 evidence；不要直接向其他目录写入输出。

可运行的参考实现是 `backend/omnitest/adapters/filecheck.py`。它通过 pyproject.toml entry point 接入，没有在 Registry 的内置列表中注册，也没有增加 Runner 分支。示例文件校验用例和自动化测试会实际读取 fixture，并验证统一结果。

领域断言通过 `runner.assertions.register("operator_name", callable)` 注册。callable 接收实际值和 expected，返回确定布尔结果；不能返回模型主观判断。注册名冲突会拒绝。当前断言插件在程序组装时注册，尚未提供独立的断言 entry point 自动发现。

插件测试应覆盖：实际领域行为、非法配置、清理、超时／取消、输出可序列化、证据范围和敏感数据。不要以固定返回 PASSED 替代真实行为；适配器只返回观察值，通过与否交由统一断言和生命周期判断。
