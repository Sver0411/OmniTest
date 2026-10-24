# 可选 AI 扩展

Phase 1 不调用大模型，不加载 provider，不读取任何模型 API Key。`backend/omnitest/ai/contracts.py` 仅定义独立的 AIProvider Protocol：

- `propose_cases(requirement, context) -> list[TestIR]`
- `analyze_failure(report) -> str`

AISettings 默认 enabled=false、provider=None。所有核心服务、适配器和 CLI 均不导入 ai 包；自动化测试检查这种依赖关系。删除 ai 目录不会破坏 Phase 1 执行路径。

Phase 3 将在 API 组装边缘引入 provider 选择和凭据配置，provider 输出必须先通过 TestIR Schema、项目引用和目标授权校验，再作为候选用例供用户保存。执行仍由现有任务服务完成，模型无权指定 executable、shell、任意 Python 或绕过服务器白名单。

失败分析和报告总结只读取已脱敏的真实报告；分析结论与确定性断言分开保存，不能重写 PASSED／FAILED 状态。修复建议作为新用例版本，经再次运行验证后才可采用。切换或关闭 provider 不改变已保存用例和运行快照。

自然语言测试设计、覆盖建议、脚本修复、Provider 管理 UI 等均尚未实现，本版本不以静态按钮冒充这些能力。
