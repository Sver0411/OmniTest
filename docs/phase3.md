# Phase 3：可选 AI 增强

实现 Provider 管理、自然语言测试设计、候选用例生成、失败分析、覆盖建议、修复建议、报告总结。默认关闭，没有模型配置也可启动核心服务。

## 配置

服务启动前设置：

```powershell
$env:OMNITEST_ALLOWED_HOSTS = "api.openai.com"
$env:OMNITEST_SECRET_AI_KEY = "your-key"
omnitest serve
```

在“AI 辅助”中添加 Provider：

```json
{"name":"My provider","base_url":"https://api.openai.com/v1","model":"your-accessible-model","key_env":"OMNITEST_SECRET_AI_KEY","timeout":30}
```

模型名由服务方与账户权限决定，不内置默认调用。支持 OpenAI-compatible Chat Completions JSON 协议，可替换 endpoint、模型和密钥引用；本地兼容服务允许 key_env 为空。远程服务要求 HTTPS 和管理员主机白名单。密钥仅在环境变量中，不保存在数据库或浏览器。

请求采用 messages 与 response_format=json_object，所选服务需支持该协议。参考 [官方 Chat API 文档](https://developers.openai.com/api/reference/resources/chat)。没有实现其他厂商原生 API SDK。

选择 Provider 后需明确启用。切换 Provider 自动关闭开关。点击生成才会发送脱敏的需求、目标、已有用例和所选报告，请仅为允许传输的数据启用。

## 操作与工作流

- **设计／生成**：输入需求或粘贴接口资料，生成方案和候选 Test IR。
- **失败分析**：选择真实运行，分析日志、断言和错误。
- **覆盖建议**：根据当前项目已有用例推荐缺失边界。
- **修复建议**：选择用例及包含该用例失败的报告，提出修正后的新候选，保留原目标和适配器。
- **报告总结**：根据真实报告生成文字，不修改执行状态。

本版本使用文本和证据清单，不传输截图二进制做视觉分析，不自动抓取外部站点。

输出必须是 `{summary, suggestions, cases}`。候选经 Test IR Schema、项目归属、目标授权、适配器与步骤能力校验，保存为 PROPOSED，尚不能执行。用户可展开 JSON 审阅，再点击“采纳为新用例”或“采纳为新用例并运行验证”。后者提交真实引擎任务并显示 verification_run_id 和实际状态。

模型不能提交任意 Python、Shell 或 executable；CLI 继续受管理员白名单限制。采纳与提交使用一个数据库事务，候选只能采纳一次，采纳时重新检查目标授权。修复不覆盖原用例，原始失败报告保持不变。

## API

- GET／PUT `/api/ai/settings`：enabled、provider_id
- GET／POST `/api/ai/providers`，PUT／DELETE `/api/ai/providers/{id}`
- POST `/api/ai/tasks`：action=design／generate／analyze／coverage／repair／summarize；project_id，以及 requirement、target_id、case_id、run_id
- GET `/api/ai/history?project_id=...`
- POST `/api/ai/history/{id}/accept`：`{"verify":false}` 或 `{"verify":true}`

关闭状态返回 409；非法内容、目标范围或 Provider 故障返回明确错误，不使用伪造建议降级。请求超时可配置，响应上限 2 MB，上下文上限 250 KB，模型请求并发上限 2，无自动重试，避免重复计费。

## 独立性与验证边界

AI 通过 `omnitest.extensions` entry point 挂载。通用 API 加载器不直接导入 AI，Runner、编排器和适配器不依赖 AI。插件缺失或加载失败不影响核心启动；配置、建议和采纳结果持久化为 resources 表中独立 kind，无需更改任务表。

关闭开关不删除既有用例，也不取消已经提交的自动化任务。移除扩展后 AI 路由不可用，自动化仍可运行。

自动测试使用真实本地 HTTP 兼容接口 fixture 验证协议与六类工作流，包括采纳后真实 CLI 运行、原失败状态保留、脱敏、非法候选、Provider 故障、配置恢复与插件移除。浏览器验证配置编辑、选择、启停。

没有配置真实模型密钥，因此没有调用外部模型，也没有验证建议语义质量或视觉分析能力。模型输出仅是建议，通过与否始终由自动化断言判断。

2026-10-24 本地完整回归：69 passed；前端格式检查、TypeScript 与生产构建通过。JUnit 记录位于本地 `data/phase3-tests.xml`，AI 管理界面截图位于 `data/ai-workspace.png`，均不提交为源代码。
