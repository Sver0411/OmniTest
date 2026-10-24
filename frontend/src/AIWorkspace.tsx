import { useEffect, useState } from "react";

type Props = {
  project: string;
  api: (path: string, init?: RequestInit) => Promise<any>;
  onError: (message: string) => void;
};

const actions: Record<string, string> = {
  design: "自然语言测试设计",
  generate: "生成候选用例",
  analyze: "失败分析",
  coverage: "测试覆盖建议",
  repair: "修复建议",
  summarize: "报告总结",
};
const initialProvider = {
  name: "兼容 API 服务",
  base_url: "https://api.openai.com/v1",
  model: "",
  key_env: "OMNITEST_SECRET_AI_KEY",
  timeout: 30,
};

export default function AIWorkspace({ project, api, onError }: Props) {
  const [state, setState] = useState<any>({
    enabled: false,
    provider_id: null,
  });
  const [providers, setProviders] = useState<any[]>([]);
  const [providerText, setProviderText] = useState(
    JSON.stringify(initialProvider, null, 2),
  );
  const [editing, setEditing] = useState("");
  const [targets, setTargets] = useState<any[]>([]);
  const [cases, setCases] = useState<any[]>([]);
  const [runs, setRuns] = useState<any[]>([]);
  const [history, setHistory] = useState<any[]>([]);
  const [action, setAction] = useState("design");
  const [requirement, setRequirement] = useState("");
  const [target, setTarget] = useState("");
  const [testCase, setTestCase] = useState("");
  const [run, setRun] = useState("");
  const [busy, setBusy] = useState(false);
  const [verification, setVerification] = useState<Record<string, any>>({});
  async function work(fn: () => Promise<void>) {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function load() {
    const [s, ps] = await Promise.all([
      api("/ai/settings"),
      api("/ai/providers"),
    ]);
    setState(s);
    setProviders(ps);
    if (project) {
      const query = "?project_id=" + project;
      const [ts, cs, rs, hs] = await Promise.all([
        api("/resources/targets" + query),
        api("/resources/cases" + query),
        api("/runs" + query),
        api("/ai/history" + query),
      ]);
      setTargets(ts);
      setCases(cs);
      setRuns(rs);
      setHistory(hs);
    } else {
      setTargets([]);
      setCases([]);
      setRuns([]);
      setHistory([]);
    }
  }
  useEffect(() => {
    setTarget("");
    setTestCase("");
    setRun("");
    work(load);
  }, [project]);
  useEffect(() => {
    const ids = history.map((h) => h.verification_run_id).filter(Boolean);
    if (!ids.length) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const values = await Promise.all(ids.map((id) => api("/runs/" + id)));
        if (!cancelled)
          setVerification(Object.fromEntries(values.map((v) => [v.id, v])));
      } catch (e) {
        if (!cancelled) onError(String(e));
      }
    };
    poll();
    const timer = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [history]);
  return (
    <div className="ai-workspace">
      <section className="panel settings-card">
        <h2>Provider 配置</h2>
        <p>
          默认关闭。启用后，点击生成才会将当前项目的脱敏上下文发送到所选服务。密钥仅引用服务器环境变量。
        </p>
        <div className="toolbar">
          <select
            aria-label="AI Provider"
            value={state.provider_id || ""}
            disabled={busy}
            onChange={(e) =>
              work(async () => {
                const next = {
                  enabled: false,
                  provider_id: e.target.value || null,
                };
                setState(
                  await api("/ai/settings", {
                    method: "PUT",
                    body: JSON.stringify(next),
                  }),
                );
              })
            }
          >
            <option value="">未选择</option>
            {providers.map((p) => (
              <option value={p.id} key={p.id}>
                {p.name} · {p.model}
              </option>
            ))}
          </select>
          <label>
            <input
              aria-label="启用 AI"
              type="checkbox"
              checked={state.enabled}
              disabled={busy || !state.provider_id}
              onChange={(e) => {
                const next = { ...state, enabled: e.target.checked };
                setState(next);
                work(async () => {
                  try {
                    setState(
                      await api("/ai/settings", {
                        method: "PUT",
                        body: JSON.stringify(next),
                      }),
                    );
                  } catch (error) {
                    await load();
                    throw error;
                  }
                });
              }}
            />{" "}
            启用 AI
          </label>
          <button
            disabled={busy || !state.provider_id}
            onClick={() => {
              const p = providers.find((p) => p.id === state.provider_id);
              const { id, ...body } = p;
              setEditing(id);
              setProviderText(JSON.stringify(body, null, 2));
            }}
          >
            编辑所选
          </button>
          <button
            disabled={busy || !state.provider_id}
            onClick={() =>
              work(async () => {
                const id = state.provider_id;
                await api("/ai/settings", {
                  method: "PUT",
                  body: JSON.stringify({ enabled: false, provider_id: null }),
                });
                await api("/ai/providers/" + id, { method: "DELETE" });
                await load();
              })
            }
          >
            删除所选
          </button>
        </div>
        <details>
          <summary>添加或编辑 Provider</summary>
          <p>
            远程主机必须先加入
            OMNITEST_ALLOWED_HOSTS。模型名称由你使用的服务决定。
          </p>
          <textarea
            aria-label="Provider JSON"
            value={providerText}
            onChange={(e) => setProviderText(e.target.value)}
          />
          <button
            disabled={busy}
            onClick={() =>
              work(async () => {
                await api("/ai/providers" + (editing ? "/" + editing : ""), {
                  method: editing ? "PUT" : "POST",
                  body: JSON.stringify(JSON.parse(providerText)),
                });
                setEditing("");
                await load();
              })
            }
          >
            保存 Provider
          </button>
          <button
            onClick={() => {
              setEditing("");
              setProviderText(JSON.stringify(initialProvider, null, 2));
            }}
          >
            新建配置
          </button>
        </details>
      </section>
      <section className="panel settings-card">
        <h2>智能辅助</h2>
        <div className="execution-options">
          <label>
            操作
            <select
              aria-label="AI 操作"
              value={action}
              onChange={(e) => setAction(e.target.value)}
            >
              {Object.entries(actions).map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            目标
            <select
              aria-label="AI 目标"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
            >
              <option value="">项目内目标</option>
              {targets.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.body.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            用例
            <select
              aria-label="AI 用例"
              value={testCase}
              onChange={(e) => setTestCase(e.target.value)}
            >
              <option value="">未选择</option>
              {cases.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.body.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            运行报告
            <select
              aria-label="AI 报告"
              value={run}
              onChange={(e) => setRun(e.target.value)}
            >
              <option value="">未选择</option>
              {runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.id.slice(0, 8)} · {r.status}
                </option>
              ))}
            </select>
          </label>
        </div>
        <textarea
          aria-label="测试需求"
          placeholder="描述测试需求，或对分析和修复补充说明。"
          value={requirement}
          onChange={(e) => setRequirement(e.target.value)}
        />
        <button
          className="primary"
          disabled={busy || !project || !state.enabled}
          onClick={() =>
            work(async () => {
              await api("/ai/tasks", {
                method: "POST",
                body: JSON.stringify({
                  action,
                  project_id: project,
                  requirement,
                  target_id: target || null,
                  case_id: testCase || null,
                  run_id: run || null,
                }),
              });
              await load();
            })
          }
        >
          {busy ? "处理中…" : "生成建议"}
        </button>
        {!project && <p>请先选择项目。</p>}
      </section>
      <section className="panel settings-card">
        <h2>建议与候选用例</h2>
        {!history.length && (
          <p>尚无建议记录。生成结果不会自动保存为用例或执行。</p>
        )}
        {history.map((h) => (
          <article className="ai-record" key={h.id}>
            <h3>
              {actions[h.action]} ·{" "}
              {h.status === "ACCEPTED" ? "已采纳" : "待审阅"}
            </h3>
            <p className="ai-text">{h.summary}</p>
            <ul>
              {h.suggestions.map((s: string, i: number) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
            {!!h.cases.length && (
              <details>
                <summary>审阅 {h.cases.length} 个候选用例</summary>
                <pre>{JSON.stringify(h.cases, null, 2)}</pre>
              </details>
            )}
            {h.status === "PROPOSED" && !!h.cases.length && (
              <div className="heading-actions">
                {[false, true].map((verify) => (
                  <button
                    key={String(verify)}
                    disabled={busy}
                    onClick={() =>
                      work(async () => {
                        await api("/ai/history/" + h.id + "/accept", {
                          method: "POST",
                          body: JSON.stringify({ verify }),
                        });
                        await load();
                      })
                    }
                  >
                    {verify ? "采纳为新用例并运行验证" : "采纳为新用例"}
                  </button>
                ))}
              </div>
            )}
            {h.verification_run_id && (
              <p>
                引擎验证：
                {verification[h.verification_run_id]?.status || "加载中"} ·{" "}
                {h.verification_run_id}
              </p>
            )}
            <small>
              {h.model} · {new Date(h.created_at * 1000).toLocaleString()}
            </small>
          </article>
        ))}
      </section>
    </div>
  );
}
