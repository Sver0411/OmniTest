# 可选 AI 扩展

第三阶段配置和工作流见 [Phase 3](phase3.md)。

AIProvider 协议定义 complete(system, context) -> dict；CompatibleProvider 实现兼容 Chat Completions 传输。AIService 负责脱敏、结构化校验、候选保存和采纳验证。当前组装兼容协议 Provider，尚无其他原生 Provider 自动发现。

AI 通过 omnitest.extensions 插件入口挂载，不参与自动化通过／失败判断。默认关闭；移除该扩展不破坏核心执行。生成内容须转换为 Test IR，经用户采纳后由既有执行器与断言引擎验证。
