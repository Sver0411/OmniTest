import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  Boxes,
  Braces,
  CalendarClock,
  Check,
  ChevronRight,
  CircleDot,
  Copy,
  FileCheck2,
  FolderKanban,
  Layers3,
  LayoutDashboard,
  Moon,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  Sun,
  Trash2,
  X,
  Users,
} from "lucide-react";
import "./styles.css";
import AIWorkspace from "./AIWorkspace";
import Operations from "./Operations";
import ScheduleEditor from "./ScheduleEditor";
import DatasetImport from "./DatasetImport";
import DatasetBinding from "./DatasetBinding";
import EnvironmentEditor from "./EnvironmentEditor";
import Collaboration from "./Collaboration";

type Resource = {
  id: string;
  project_id: string | null;
  kind: string;
  body: Record<string, any>;
};
type Run = {
  id: string;
  project_id: string;
  status: string;
  created_at: number;
  started_at?: number;
  finished_at?: number;
  summary: any;
  results?: any[];
  node_id?: string | null;
  cancel_requested?: boolean;
};
const names: Record<string, string> = {
  dashboard: "工作台",
  projects: "项目管理",
  targets: "测试目标",
  cases: "测试用例",
  suites: "测试套件",
  execution: "测试执行",
  reports: "测试报告",
  adapters: "执行适配器",
  schedules: "定时任务",
  settings: "系统设置",
  ai: "AI 辅助",
  environments: "测试环境",
  datasets: "数据集",
  operations: "运行维护",
  collaboration: "成员与团队",
};
const icons: Record<string, any> = {
  dashboard: LayoutDashboard,
  projects: FolderKanban,
  targets: CircleDot,
  cases: FileCheck2,
  suites: Layers3,
  execution: Play,
  reports: Activity,
  adapters: Boxes,
  schedules: CalendarClock,
  settings: Settings2,
  ai: Boxes,
  environments: Braces,
  datasets: Braces,
  operations: Activity,
  collaboration: Users,
};
const date = (t?: number) =>
  t ? new Date(t * 1000).toLocaleString("zh-CN", { hour12: false }) : "—";
const short = (id: string) => id.slice(0, 8);
const pretty = (v: any) => JSON.stringify(v, null, 2);
const terminal = (s: string) => !["PENDING", "RUNNING"].includes(s);

async function api(path: string, init?: RequestInit) {
  const token = sessionStorage.getItem("omnitest-token");
  const response = await fetch("/api" + path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: "Bearer " + token } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : pretty(body.detail || response.statusText),
    );
  }
  return response.status === 204 ? null : response.json();
}
async function download(path: string, filename: string) {
  const token = sessionStorage.getItem("omnitest-token");
  const response = await fetch("/api" + path, {
    headers: token ? { Authorization: "Bearer " + token } : {},
  });
  if (!response.ok) throw new Error("下载失败：" + response.status);
  const url = URL.createObjectURL(await response.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
function Badge({ status }: { status: string }) {
  return <span className={"badge " + status.toLowerCase()}>{status}</span>;
}
function Empty({ text, action }: { text: string; action?: React.ReactNode }) {
  return (
    <div className="empty">
      <h3>{text}</h3>
      {action}
    </div>
  );
}

function App() {
  const [page, setPage] = useState(location.hash.slice(1) || "dashboard");
  const [theme, setTheme] = useState(
    localStorage.getItem("omnitest-theme") || "light",
  );
  const [project, setProject] = useState("");
  const [projects, setProjects] = useState<Resource[]>([]);
  const [rows, setRows] = useState<Resource[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [dashboard, setDashboard] = useState<any>(null);
  const [adapters, setAdapters] = useState<any[]>([]);
  const [system, setSystem] = useState<any>(null);
  const permissions = system?.principal?.permissions || {
    admin: false,
    manage: false,
    execute: false,
  };
  const [credentialEpoch, setCredentialEpoch] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [search, setSearch] = useState("");
  const [filterAdapter, setFilterAdapter] = useState("");
  const [filterSince, setFilterSince] = useState("");
  const [filterUntil, setFilterUntil] = useState("");
  const [editor, setEditor] = useState<{
    kind: string;
    row?: Resource;
    body: string;
  } | null>(null);
  const [report, setReport] = useState<Run | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [environment, setEnvironment] = useState("");
  const [datasetOptions, setDatasetOptions] = useState<Resource[]>([]);
  const [datasetFile, setDatasetFile] = useState<File | null>(null);
  const [binding, setBinding] = useState<Resource | null>(null);
  const [editorValid, setEditorValid] = useState(true);
  const [environments, setEnvironments] = useState<Resource[]>([]);
  const [concurrency, setConcurrency] = useState(1);
  const [nodes, setNodes] = useState<any[]>([]);
  const [node, setNode] = useState("");
  const [token, setToken] = useState(
    sessionStorage.getItem("omnitest-token") || "",
  );
  function saveToken() {
    sessionStorage.setItem("omnitest-token", token);
    refreshVersion.current += 1;
    setSystem(null);
    setProjects([]);
    setRows([]);
    setRuns([]);
    setDashboard(null);
    setTargets([]);
    setCaseOptions([]);
    setSuiteOptions([]);
    setEnvironments([]);
    setDatasetOptions([]);
    setDatasetFile(null);
    setBinding(null);
    setNodes([]);
    setNode("");
    setReport(null);
    setEditor(null);
    setProject("");
    setSelected([]);
    setError("");
    setCredentialEpoch((value) => value + 1);
  }
  const [targets, setTargets] = useState<Resource[]>([]);
  const [suiteOptions, setSuiteOptions] = useState<Resource[]>([]);
  const [caseOptions, setCaseOptions] = useState<Resource[]>([]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("omnitest-theme", theme);
  }, [theme]);
  useEffect(() => {
    const fn = () => setPage(location.hash.slice(1) || "dashboard");
    addEventListener("hashchange", fn);
    return () => removeEventListener("hashchange", fn);
  }, []);
  const go = (p: string) => {
    location.hash = p;
    setSearch("");
    setSelected([]);
  };
  const refreshVersion = useRef(0);
  async function refresh(quiet = false) {
    const version = ++refreshVersion.current;
    if (!quiet) setLoading(true);
    try {
      const query = project ? "?project_id=" + project : "";
      const params = new URLSearchParams();
      if (project) params.set("project_id", project);
      if (filterAdapter) params.set("adapter", filterAdapter);
      if (filterSince)
        params.set("since", String(new Date(filterSince).getTime() / 1000));
      if (filterUntil)
        params.set(
          "until",
          String(new Date(filterUntil).getTime() / 1000 + 86399),
        );
      const resourcePage = [
        "cases",
        "suites",
        "schedules",
        "targets",
        "environments",
        "datasets",
        "projects",
      ].includes(page);
      const [ps, ds, ss, ads, nextRuns, nextRows, projectResources, nextNodes] =
        await Promise.all([
          api("/resources/projects"),
          api("/dashboard" + query),
          api("/settings" + query),
          api("/adapters"),
          ["execution", "reports", "dashboard"].includes(page)
            ? api("/runs?" + params)
            : Promise.resolve([]),
          resourcePage
            ? api("/resources/" + page + (page === "projects" ? "" : query))
            : Promise.resolve([]),
          project
            ? Promise.all(
                ["targets", "environments", "suites", "cases", "datasets"].map(
                  (k) => api("/resources/" + k + query),
                ),
              )
            : Promise.resolve([[], [], [], [], []]),
          project ? api("/nodes" + query) : Promise.resolve([]),
        ]);
      if (version !== refreshVersion.current) return;
      setProjects(ps);
      setDashboard(ds);
      setSystem(ss);
      setAdapters(ads.items);
      setRuns(nextRuns);
      setRows(nextRows);
      setNodes(nextNodes);
      const [ts, es, suites, cases, datasets] = projectResources;
      setTargets(ts);
      setEnvironments(es);
      setSuiteOptions(suites);
      setCaseOptions(cases);
      setDatasetOptions(datasets);
      if (ads.errors.length) setError(ads.errors.join("; "));
    } catch (e) {
      if (version === refreshVersion.current) setError(String(e));
    } finally {
      if (version === refreshVersion.current) setLoading(false);
    }
  }
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 4000);
    return () => clearTimeout(timer);
  }, [notice]);
  useEffect(() => {
    setSelected([]);
    setEnvironment("");
    setNode("");
    refresh();
  }, [page, project, filterAdapter, filterSince, filterUntil, credentialEpoch]);
  useEffect(() => {
    const timer = setInterval(() => {
      if (["dashboard", "execution", "reports"].includes(page)) refresh(true);
    }, 3000);
    return () => clearInterval(timer);
  }, [page, project, filterAdapter, filterSince, filterUntil]);
  useEffect(() => {
    if (!report || terminal(report.status)) return;
    const timer = setInterval(
      () =>
        api("/runs/" + report.id)
          .then(setReport)
          .catch((e) => setError(String(e))),
      1000,
    );
    return () => clearInterval(timer);
  }, [report?.id, report?.status]);
  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  function template(kind: string): any {
    switch (kind) {
      case "projects":
        return { name: "新测试项目", description: "" };
      case "targets":
        return {
          name: "本地 HTTP 目标",
          kind: "http",
          authorized: false,
          config: { base_url: "http://127.0.0.1:8765", allow_mutations: false },
        };
      case "environments":
        return { name: "Local", variables: {}, target_overrides: {} };
      case "datasets":
        return {
          name: "新数据集",
          description: "",
          rows: [{ input: "", expected: "" }],
        };
      case "cases":
        return {
          name: "新 API 测试",
          adapter: "api",
          target: targets[0]?.id || "",
          steps: [
            {
              id: "request",
              action: "request",
              config: { path: "/health", method: "GET" },
              assertions: [{ path: "$.status_code", op: "eq", expected: 200 }],
            },
          ],
          parameters: {},
          datasets: [],
          assertions: [],
          timeout: 30,
          retries: 0,
          tags: ["functional"],
        };
      case "suites":
        return {
          name: "新测试套件",
          case_ids: caseOptions.slice(0, 1).map((c) => c.id),
          concurrency: 1,
          parameters: {},
        };
      case "schedules":
        return {
          name: "定时回归",
          suite_id: suiteOptions[0]?.id || "",
          environment_id: null,
          node_id: null,
          interval_seconds: 3600,
          schedule_type: "interval",
          timezone:
            Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai",
          overlap: "skip",
          misfire_policy: "coalesce",
          misfire_grace_seconds: 60,
          enabled: true,
        };
    }
  }
  function openEditor(kind: string, row?: Resource, copy = false) {
    if (!permissions.manage) return;
    let body = row ? structuredClone(row.body) : template(kind);
    if (copy) {
      delete body.id;
      body.name += " · 副本";
    }
    setEditor({ kind, row: copy ? undefined : row, body: pretty(body) });
    setEditorValid(true);
  }
  async function saveEditor() {
    if (!editor) return;
    const body = JSON.parse(editor.body);
    if (Array.isArray(body)) {
      if (editor.kind !== "cases" || editor.row)
        throw new Error("仅测试用例支持批量导入");
      await api("/cases/import", {
        method: "POST",
        body: pretty({ project_id: project, cases: body }),
      });
    } else
      await api(
        "/resources/" + editor.kind + (editor.row ? "/" + editor.row.id : ""),
        {
          method: editor.row ? "PUT" : "POST",
          body: pretty({
            project_id:
              editor.kind === "projects"
                ? null
                : project || editor.row?.project_id,
            body,
          }),
        },
      );
    setEditor(null);
    setNotice("已保存");
    await refresh();
  }
  async function startRun(payload: any) {
    if (!permissions.execute) throw new Error("当前凭据没有执行权限");
    const value = await api("/runs", {
      method: "POST",
      body: pretty({
        project_id: project,
        environment_id: environment || null,
        node_id: node || null,
        ...payload,
      }),
    });
    setReport(await api("/runs/" + value.id));
    setNotice("任务已进入执行队列");
    await refresh(true);
  }
  const filtered = rows
    .filter(
      (r) =>
        r.kind === page &&
        (page === "projects" || !project || r.project_id === project),
    )
    .filter((r) =>
      (r.body.name + pretty(r.body.tags || []))
        .toLowerCase()
        .includes(search.toLowerCase()),
    );
  const visibleRuns = runs.filter(
    (r) =>
      r.id.includes(search) ||
      r.status.toLowerCase().includes(search.toLowerCase()),
  );
  const createButton = (kind: string) =>
    !permissions.manage ||
    (kind === "projects" && !permissions.admin) ? null : (
      <button
        className="primary"
        disabled={busy || (kind !== "projects" && !project)}
        onClick={() => openEditor(kind)}
      >
        <Plus size={16} />
        新建{names[kind]?.replace("管理", "").replace("测试", "")}
      </button>
    );
  const runTable = (items: Run[], compact = false) =>
    items.length ? (
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>执行任务</th>
              <th>状态</th>
              <th>执行节点</th>
              <th>通过 / 总数</th>
              <th>开始时间</th>
              <th>耗时</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((r) => (
              <tr
                key={r.id}
                onClick={() =>
                  act(async () => setReport(await api("/runs/" + r.id)))
                }
                className="clickable"
              >
                <td>
                  <div className="run-name">
                    <div>
                      <strong>Run #{short(r.id)}</strong>
                      <small>
                        {projects.find((p) => p.id === r.project_id)?.body
                          .name || short(r.project_id)}
                      </small>
                    </div>
                  </div>
                </td>
                <td>
                  <Badge status={r.status} />
                </td>
                <td>{r.node_id || "本机"}</td>
                <td>
                  <strong>
                    {r.summary.total !== undefined
                      ? (r.summary.counts?.PASSED ?? 0)
                      : "—"}
                  </strong>
                  <span className="muted"> / {r.summary.total ?? "—"}</span>
                </td>
                <td className="muted">{date(r.created_at)}</td>
                <td>{r.summary.duration?.toFixed(2) ?? "—"}s</td>
                <td>
                  <ChevronRight size={16} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {compact && (
          <button className="table-footer" onClick={() => go("reports")}>
            查看所有测试报告 <ArrowRight size={14} />
          </button>
        )}
      </div>
    ) : (
      <Empty
        text="尚无测试运行记录"
        action={
          <button onClick={() => go("execution")}>
            前往测试执行 <ArrowRight size={14} />
          </button>
        }
      />
    );

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#dashboard">
          OmniTest
        </a>
        <nav>
          {[
            "dashboard",
            "projects",
            "targets",
            "cases",
            "suites",
            "execution",
            "reports",
          ].map((p) => {
            const Icon = icons[p];
            return (
              <button
                className={page === p ? "active" : ""}
                key={p}
                onClick={() => go(p)}
              >
                <Icon size={18} />
                {names[p]}
              </button>
            );
          })}
          <div className="nav-divider" />
          {[
            "adapters",
            "schedules",
            "environments",
            "datasets",
            "ai",
            "operations",
            "collaboration",
            "settings",
          ]
            .filter(
              (p) =>
                !["ai", "operations", "collaboration"].includes(p) ||
                permissions.admin,
            )
            .map((p) => {
              const Icon = icons[p];
              return (
                <button
                  className={page === p ? "active" : ""}
                  key={p}
                  onClick={() => go(p)}
                >
                  <Icon size={18} />
                  {names[p]}
                </button>
              );
            })}
        </nav>
      </aside>
      <main>
        <header className="topbar">
          <label className="project-selector">
            项目
            <select
              aria-label="筛选项目"
              value={project}
              onChange={(e) => setProject(e.target.value)}
            >
              <option value="">全部项目</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.body.name}
                </option>
              ))}
            </select>
          </label>
          {system?.principal && (
            <span aria-label="当前访问身份">
              {system.principal.name} ·{" "}
              {
                {
                  admin: "管理员",
                  editor: "编辑者",
                  operator: "执行者",
                  viewer: "只读",
                  member: "成员",
                }[system.principal.role as string]
              }
            </span>
          )}
          <button
            className="icon-button"
            aria-label="切换主题"
            title="切换深色与浅色"
            onClick={() => setTheme(theme === "light" ? "dark" : "light")}
          >
            {theme === "light" ? <Moon size={18} /> : <Sun size={18} />}
          </button>
        </header>
        <div className="content">
          <div className="page-heading">
            <div>
              <h1>{names[page] || "工作台"}</h1>
              {page === "dashboard" && (
                <p>通过率与失败数统计最近 30 次运行。</p>
              )}
            </div>
            <div className="heading-actions">
              <button onClick={() => refresh()} disabled={loading}>
                <RefreshCw size={15} className={loading ? "spin" : ""} />
                刷新
              </button>
              {page === "dashboard" && permissions.execute ? (
                <button className="primary" onClick={() => go("execution")}>
                  <Play size={15} />
                  启动测试
                </button>
              ) : [
                  "cases",
                  "suites",
                  "projects",
                  "targets",
                  "environments",
                  "schedules",
                ].includes(page) ? (
                createButton(page)
              ) : null}
            </div>
          </div>
          {error && (
            <div role="alert" className="alert">
              <strong>操作未完成</strong>
              <pre>{error}</pre>
              <button onClick={() => setError("")} aria-label="关闭错误">
                <X size={16} />
              </button>
            </div>
          )}
          {notice && (
            <div className="notice">
              <Check size={16} />
              {notice}
              <button onClick={() => setNotice("")} aria-label="关闭通知">
                <X size={14} />
              </button>
            </div>
          )}
          {loading && !dashboard ? (
            <div className="empty">
              <RefreshCw className="spin" />
              正在连接自动化服务…
            </div>
          ) : (
            <>
              {page === "dashboard" && dashboard && (
                <>
                  <div className="metrics">
                    {[
                      { label: "项目", value: dashboard.projects },
                      { label: "用例", value: dashboard.cases },
                      {
                        label: "通过率",
                        value:
                          dashboard.pass_rate === null
                            ? "—"
                            : (dashboard.pass_rate * 100).toFixed(1) + "%",
                      },
                      {
                        label: "失败与异常",
                        value:
                          (dashboard.counts.FAILED || 0) +
                          (dashboard.counts.ERROR || 0),
                      },
                    ].map((m) => (
                      <article className="metric" key={m.label}>
                        <p>{m.label}</p>
                        <h2>{m.value}</h2>
                      </article>
                    ))}
                  </div>
                  <section className="panel recent">
                    <div className="panel-heading">
                      <div>
                        <h2>最近执行</h2>
                      </div>
                    </div>
                    {runTable(runs.slice(0, 6), true)}
                  </section>
                  <section className="panel trend-panel">
                    <div className="panel-heading">
                      <h2>通过率趋势</h2>
                      <span className="muted">每次运行</span>
                    </div>
                    {dashboard.recent.length ? (
                      <div className="chart">
                        <div className="chart-labels">
                          <span>100%</span>
                          <span>50%</span>
                          <span>0%</span>
                        </div>
                        <div className="chart-bars">
                          {[...dashboard.recent].reverse().map((r: Run) => (
                            <button
                              aria-label={`查看运行 ${short(r.id)}`}
                              title={`${date(r.created_at)} · ${((r.summary.pass_rate || 0) * 100).toFixed(1)}%`}
                              key={r.id}
                              onClick={() =>
                                act(async () =>
                                  setReport(await api("/runs/" + r.id)),
                                )
                              }
                            >
                              <i
                                style={{
                                  height:
                                    Math.max(
                                      2,
                                      (r.summary.pass_rate || 0) * 100,
                                    ) + "%",
                                }}
                                className={r.status.toLowerCase()}
                              />
                              <small>
                                {new Date(
                                  r.created_at * 1000,
                                ).toLocaleTimeString("zh-CN", {
                                  hour: "2-digit",
                                  minute: "2-digit",
                                })}
                              </small>
                            </button>
                          ))}
                        </div>
                      </div>
                    ) : (
                      <Empty text="暂无趋势数据" />
                    )}
                  </section>
                </>
              )}
              {[
                "projects",
                "targets",
                "cases",
                "suites",
                "schedules",
                "environments",
                "datasets",
              ].includes(page) && (
                <section className="panel">
                  <div className="toolbar">
                    <div className="search">
                      <Search size={16} />
                      <input
                        aria-label="搜索资源"
                        placeholder="搜索名称或标签…"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                      />
                    </div>
                    <span className="muted">{filtered.length} 条记录</span>
                    <div className="spacer" />
                    {page === "datasets" && permissions.manage && (
                      <label className="button">
                        导入 CSV / JSON
                        <input
                          aria-label="导入数据集文件"
                          type="file"
                          accept=".csv,.json"
                          hidden
                          disabled={!project}
                          onChange={(event) => {
                            const file = event.target.files?.[0];
                            if (file) setDatasetFile(file);
                            event.target.value = "";
                          }}
                        />
                      </label>
                    )}
                    {page === "cases" && permissions.manage && (
                      <>
                        <label className="button">
                          <ArrowDownToLine size={15} />
                          导入 JSON
                          <input
                            type="file"
                            accept=".json"
                            hidden
                            onChange={(e) => {
                              const file = e.target.files?.[0];
                              if (file)
                                act(async () => {
                                  const body = JSON.parse(await file.text());
                                  setEditor({
                                    kind: "cases",
                                    body: pretty(body),
                                  });
                                });
                              e.target.value = "";
                            }}
                          />
                        </label>
                        <button
                          disabled={!rows.length}
                          onClick={() => {
                            const data = new Blob(
                              [pretty(rows.map((r) => r.body))],
                              { type: "application/json" },
                            );
                            const url = URL.createObjectURL(data);
                            const a = document.createElement("a");
                            a.href = url;
                            a.download = "test-cases.json";
                            a.click();
                            URL.revokeObjectURL(url);
                          }}
                        >
                          <ArrowDownToLine size={15} />
                          导出
                        </button>
                      </>
                    )}
                  </div>
                  {!project && page !== "projects" && (
                    <div className="inline-info">
                      选择右上角的项目后即可新建或运行测试。
                    </div>
                  )}
                  {filtered.length ? (
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>名称 / ID</th>
                            <th>{page === "cases" ? "执行器" : "配置"}</th>
                            <th>项目</th>
                            <th>操作</th>
                          </tr>
                        </thead>
                        <tbody>
                          {filtered.map((row) => (
                            <tr key={row.id}>
                              <td>
                                <strong>{row.body.name}</strong>
                                <small className="mono">{row.id}</small>
                              </td>
                              <td>
                                {page === "cases" ? (
                                  <>
                                    <span className="neutral-tag">
                                      {row.body.adapter.toUpperCase()}
                                    </span>
                                    <small>
                                      {row.body.steps.length} 个步骤 ·{" "}
                                      {row.body.timeout}s 超时
                                    </small>
                                  </>
                                ) : page === "suites" ? (
                                  `${row.body.case_ids.length} 个用例 · 并发 ${row.body.concurrency}`
                                ) : page === "schedules" ? (
                                  <>
                                    <Badge
                                      status={
                                        row.body.enabled ? "RUNNING" : "SKIPPED"
                                      }
                                    />
                                    <small>
                                      {row.body.schedule_type === "cron"
                                        ? `${row.body.cron} · ${row.body.timezone}`
                                        : `每 ${row.body.interval_seconds}s`}{" "}
                                      · 下次 {date(row.body.next_run)}
                                      {" · "}
                                      {row.body.node_id || "本机"}
                                    </small>
                                    {row.body.last_error && (
                                      <small className="error-text">
                                        {row.body.last_error}
                                      </small>
                                    )}
                                    {!!row.body.skipped_count && (
                                      <small>
                                        已跳过 {row.body.skipped_count} 次 ·{" "}
                                        {row.body.last_skip_reason}
                                      </small>
                                    )}
                                  </>
                                ) : page === "datasets" ? (
                                  `${row.body.rows.length} 行 · ${Object.keys(row.body.rows[0]).length} 列`
                                ) : page === "targets" ? (
                                  <>
                                    <span className="neutral-tag">
                                      {row.body.kind}
                                    </span>
                                    <small>
                                      {row.body.authorized
                                        ? "已授权"
                                        : "未授权"}
                                    </small>
                                  </>
                                ) : (
                                  row.body.description ||
                                  Object.keys(row.body.variables || {}).join(
                                    ", ",
                                  ) ||
                                  "—"
                                )}
                              </td>
                              <td className="muted">
                                {projects.find(
                                  (p) => p.id === (row.project_id || row.id),
                                )?.body.name || "—"}
                              </td>
                              <td>
                                <div className="row-actions">
                                  {page === "datasets" && (
                                    <button
                                      onClick={() =>
                                        act(() =>
                                          download(
                                            `/datasets/${row.id}/export?format=json`,
                                            `${row.id}.json`,
                                          ),
                                        )
                                      }
                                    >
                                      导出 JSON
                                    </button>
                                  )}
                                  {page === "cases" && (
                                    <button
                                      disabled={!permissions.manage || !project}
                                      onClick={() => setBinding(row)}
                                    >
                                      数据集
                                    </button>
                                  )}
                                  {["cases", "suites"].includes(page) && (
                                    <button
                                      aria-label="运行"
                                      title="运行"
                                      disabled={
                                        busy || !project || !permissions.execute
                                      }
                                      onClick={() =>
                                        act(() =>
                                          startRun(
                                            page === "cases"
                                              ? { case_ids: [row.id] }
                                              : { suite_id: row.id },
                                          ),
                                        )
                                      }
                                    >
                                      <Play size={15} />
                                    </button>
                                  )}
                                  <button
                                    disabled={
                                      !permissions.manage ||
                                      (page === "projects" &&
                                        !permissions.admin &&
                                        !!system?.principal?.project_roles &&
                                        system.principal.project_roles[
                                          row.id
                                        ] !== "editor")
                                    }
                                    onClick={() => {
                                      if (row.project_id)
                                        setProject(row.project_id);
                                      openEditor(page, row);
                                    }}
                                  >
                                    编辑
                                  </button>
                                  {page === "cases" && (
                                    <button
                                      aria-label="复制用例"
                                      disabled={!permissions.manage}
                                      onClick={() => {
                                        if (row.project_id)
                                          setProject(row.project_id);
                                        openEditor("cases", row, true);
                                      }}
                                    >
                                      <Copy size={15} />
                                    </button>
                                  )}
                                  <button
                                    className="danger-text"
                                    aria-label="删除"
                                    disabled={
                                      !permissions.manage ||
                                      (page === "projects" &&
                                        !permissions.admin)
                                    }
                                    onClick={() => {
                                      if (
                                        confirm(
                                          `删除“${row.body.name}”？被其他资源引用时无法删除。`,
                                        )
                                      )
                                        act(async () => {
                                          await api(
                                            "/resources/" + page + "/" + row.id,
                                            { method: "DELETE" },
                                          );
                                          await refresh();
                                        });
                                    }}
                                  >
                                    <Trash2 size={15} />
                                  </button>
                                </div>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <Empty text="还没有相关资源" action={createButton(page)} />
                  )}
                </section>
              )}
              {page === "execution" && (
                <>
                  <section className="panel execution-panel">
                    <div className="panel-heading">
                      <div>
                        <h2>启动测试运行</h2>
                        <p>选择项目和环境，再执行用例或套件。</p>
                      </div>
                      <Play size={20} />
                    </div>
                    {!project ? (
                      <div className="inline-info">
                        请先在右上角选择一个项目。
                      </div>
                    ) : (
                      <>
                        <div className="execution-options">
                          <label>
                            执行节点
                            <select
                              aria-label="执行节点"
                              value={node}
                              onChange={(e) => setNode(e.target.value)}
                            >
                              <option value="">本机</option>
                              {nodes
                                .filter((n) => n.enabled)
                                .map((n) => (
                                  <option key={n.id} value={n.id}>
                                    {n.id} ·{" "}
                                    {n.status === "online"
                                      ? "在线"
                                      : "离线，等待连接"}
                                  </option>
                                ))}
                            </select>
                          </label>
                          <label>
                            执行环境
                            <select
                              value={environment}
                              onChange={(e) => setEnvironment(e.target.value)}
                            >
                              <option value="">用例默认环境</option>
                              {environments.map((e) => (
                                <option key={e.id} value={e.id}>
                                  {e.body.name}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label>
                            用例并发上限
                            <input
                              aria-label="并发上限"
                              type="number"
                              min="1"
                              max="8"
                              value={concurrency}
                              onChange={(e) =>
                                setConcurrency(Number(e.target.value))
                              }
                            />
                          </label>
                          <button
                            className="primary"
                            disabled={
                              busy || !selected.length || !permissions.execute
                            }
                            onClick={() =>
                              act(() =>
                                startRun({ case_ids: selected, concurrency }),
                              )
                            }
                          >
                            <Play size={15} />
                            运行已选 {selected.length} 个用例
                          </button>
                        </div>
                        <div className="selection-grid">
                          {caseOptions.map((c) => (
                            <label key={c.id}>
                              <input
                                type="checkbox"
                                checked={selected.includes(c.id)}
                                onChange={(e) =>
                                  setSelected(
                                    e.target.checked
                                      ? [...selected, c.id]
                                      : selected.filter((i) => i !== c.id),
                                  )
                                }
                              />
                              <span>
                                {c.body.name}
                                <small>
                                  {c.body.adapter} · {c.body.steps.length}{" "}
                                  个步骤
                                </small>
                              </span>
                            </label>
                          ))}
                        </div>
                        <div className="suite-buttons">
                          {suiteOptions.map((s) => (
                            <button
                              key={s.id}
                              disabled={busy || !permissions.execute}
                              onClick={() =>
                                act(() => startRun({ suite_id: s.id }))
                              }
                            >
                              <Layers3 size={15} />
                              {s.body.name}
                              <Play size={14} />
                            </button>
                          ))}
                        </div>
                      </>
                    )}
                  </section>
                  <section className="panel recent">
                    <div className="panel-heading">
                      <h2>运行队列与历史</h2>
                      <span className="muted update-label">自动更新</span>
                    </div>
                    {runTable(runs)}
                  </section>
                </>
              )}
              {page === "reports" && (
                <section className="panel">
                  <div className="toolbar">
                    <div className="search">
                      <Search size={16} />
                      <input
                        aria-label="搜索运行"
                        placeholder="搜索运行 ID 或状态…"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                      />
                    </div>
                    <select
                      aria-label="筛选适配器"
                      value={filterAdapter}
                      onChange={(e) => setFilterAdapter(e.target.value)}
                    >
                      <option value="">所有适配器</option>
                      {adapters.map((a) => (
                        <option key={a.id}>{a.id}</option>
                      ))}
                    </select>
                    <input
                      aria-label="开始日期"
                      type="date"
                      value={filterSince}
                      onChange={(e) => setFilterSince(e.target.value)}
                    />
                    <input
                      aria-label="结束日期"
                      type="date"
                      value={filterUntil}
                      onChange={(e) => setFilterUntil(e.target.value)}
                    />
                  </div>
                  {runTable(visibleRuns)}
                </section>
              )}
              {page === "adapters" && (
                <div className="adapter-grid">
                  {adapters.map((a) => (
                    <article className="panel adapter-card" key={a.id}>
                      <span className="muted">版本 {a.version}</span>
                      <h2>{a.id.toUpperCase()} Adapter</h2>
                      <p>目标类型：{a.target_kinds.join(", ")}</p>
                      <p>
                        安装包：
                        {a.package
                          ? `${a.package.name} ${a.package.version}`
                          : "手动注册"}{" "}
                        · API {a.api_version}
                      </p>
                      <div className="tags">
                        {a.capabilities.map((c: string) => (
                          <span className="neutral-tag" key={c}>
                            {c}
                          </span>
                        ))}
                      </div>
                      <hr />
                      <h4>执行依赖</h4>
                      {a.requirements.map((r: string) => (
                        <p key={r}>
                          <CircleDot size={13} /> {r}
                        </p>
                      ))}
                      <small>已发现插件；执行时检查具体目标和运行环境。</small>
                    </article>
                  ))}
                </div>
              )}
              {page === "ai" && permissions.admin && (
                <AIWorkspace project={project} api={api} onError={setError} />
              )}
              {page === "collaboration" && permissions.admin && (
                <Collaboration api={api} projects={projects} />
              )}
              {page === "operations" && permissions.admin && (
                <Operations api={api} onError={setError} />
              )}
              {page === "settings" && system && (
                <div className="settings-grid">
                  <section className="panel settings-card">
                    <h2>执行配置</h2>
                    <dl>
                      <dt>并发执行上限</dt>
                      <dd>{system.max_concurrency}</dd>
                      <dt>任务调度</dt>
                      <dd>持久化间隔调度</dd>
                      <dt>CLI 程序白名单</dt>
                      <dd>
                        {permissions.admin
                          ? system.cli_programs.join(", ") || "空"
                          : "仅管理员可见"}
                      </dd>
                      <dt>远程目标白名单</dt>
                      <dd>
                        {permissions.admin
                          ? system.allowed_remote_hosts.join(", ") ||
                            "默认仅 loopback"
                          : "仅管理员可见"}
                      </dd>
                      <dt>API 认证</dt>
                      <dd>
                        {system.authentication ? "Bearer Token" : "本地访问"}
                      </dd>
                    </dl>
                    <p>
                      执行策略由服务端配置文件管理。环境变量中的凭据使用{" "}
                      <code>OMNITEST_SECRET_</code> 前缀，并在 IR 中通过 secret
                      引用。
                    </p>
                    <button onClick={() => go("environments")}>
                      管理测试环境 <ArrowRight size={14} />
                    </button>
                  </section>
                  <section className="panel settings-card">
                    <h2>API 访问令牌</h2>
                    <p>
                      当前身份：{system.principal.name}，角色：
                      {system.principal.role}。
                    </p>
                    <p>若服务端启用认证，在当前浏览器会话保存令牌。</p>
                    <input
                      aria-label="API 访问令牌"
                      type="password"
                      value={token}
                      onChange={(e) => setToken(e.target.value)}
                      placeholder="Bearer token"
                    />
                    <button onClick={saveToken}>保存到当前会话</button>
                  </section>
                </div>
              )}
              {page === "settings" && !system && (
                <section className="panel settings-card">
                  <h2>API 访问令牌</h2>
                  <p>服务启用认证时，请先配置当前浏览器会话的访问令牌。</p>
                  <input
                    aria-label="API 访问令牌"
                    type="password"
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                  />
                  <button onClick={saveToken}>保存到当前会话</button>
                </section>
              )}
            </>
          )}
        </div>
      </main>
      {editor && (
        <div className="modal-backdrop">
          <section
            className="modal editor-modal"
            role="dialog"
            aria-modal="true"
            aria-label="资源编辑器"
          >
            <div className="modal-heading">
              <div>
                <h2>
                  {editor.row ? "编辑" : "新建"}
                  {names[editor.kind]}
                </h2>
              </div>
              <button aria-label="关闭编辑器" onClick={() => setEditor(null)}>
                <X size={20} />
              </button>
            </div>
            {error && (
              <div role="alert" className="alert">
                <pre>{error}</pre>
              </div>
            )}
            {editor.kind === "environments" ? (
              <EnvironmentEditor
                body={editor.body}
                targets={targets}
                onChange={(body) => setEditor({ ...editor, body })}
                onValidity={setEditorValid}
              />
            ) : editor.kind === "schedules" ? (
              <ScheduleEditor
                body={editor.body}
                onChange={(body) => setEditor({ ...editor, body })}
                suites={suiteOptions}
                environments={environments}
                nodes={nodes}
                api={api}
              />
            ) : (
              <div className="editor-body">
                <textarea
                  aria-label="资源 JSON"
                  spellCheck={false}
                  value={editor.body}
                  onChange={(e) =>
                    setEditor({ ...editor, body: e.target.value })
                  }
                />
                <aside>
                  <h4>项目资源引用</h4>
                  {[
                    ...targets,
                    ...caseOptions,
                    ...suiteOptions,
                    ...environments,
                    ...datasetOptions,
                  ].map((r) => (
                    <div key={r.id}>
                      <strong>{r.body.name}</strong>
                      <code>{r.id}</code>
                    </div>
                  ))}
                  <h4>提示</h4>
                  <p>
                    目标必须明确授权。HTTP 写请求需设置 allow_mutations。CLI
                    只能选择服务器已配置的 program。
                  </p>
                  <p>
                    参数引用：<code>{"${name}"}</code>
                    <br />
                    凭据引用：<code>{"${secret.OMNITEST_SECRET_NAME}"}</code>
                  </p>
                  <p>
                    常用断言：eq、ne、contains、regex、gt、lt、exists、set_eq。
                  </p>
                </aside>
              </div>
            )}
            <div className="modal-footer">
              {!["schedules", "environments"].includes(editor.kind) && (
                <button
                  onClick={() => {
                    try {
                      setEditor({
                        ...editor,
                        body: pretty(JSON.parse(editor.body)),
                      });
                    } catch (e) {
                      setError(String(e));
                    }
                  }}
                >
                  <Braces size={15} />
                  格式化
                </button>
              )}
              <div className="spacer" />
              <button onClick={() => setEditor(null)}>取消</button>
              <button
                className="primary"
                disabled={busy || !editorValid}
                onClick={() => act(saveEditor)}
              >
                <Check size={15} />
                保存配置
              </button>
            </div>
          </section>
        </div>
      )}
      {datasetFile && (
        <DatasetImport
          file={datasetFile}
          project={project}
          api={api}
          onClose={() => setDatasetFile(null)}
          onSaved={() => {
            setDatasetFile(null);
            setNotice("数据集已导入");
            refresh();
          }}
        />
      )}
      {binding && (
        <DatasetBinding
          row={binding}
          datasets={datasetOptions}
          onClose={() => setBinding(null)}
          onSave={async (body) => {
            await api("/resources/cases/" + binding.id, {
              method: "PUT",
              body: pretty({ project_id: binding.project_id, body }),
            });
            setBinding(null);
            setNotice("数据集关联已保存");
            await refresh();
          }}
        />
      )}
      {report && (
        <div className="modal-backdrop">
          <section
            className="modal report-modal"
            role="dialog"
            aria-modal="true"
            aria-label="运行报告"
          >
            <div className="modal-heading">
              <div>
                <h2>
                  Run #{short(report.id)} <Badge status={report.status} />
                </h2>
                <p>
                  {date(report.created_at)} ·{" "}
                  {report.summary.duration?.toFixed(2) || "—"}s{" · "}
                  {report.node_id || "本机"}
                </p>
              </div>
              <button aria-label="关闭报告" onClick={() => setReport(null)}>
                <X size={20} />
              </button>
            </div>
            <div className="report-toolbar">
              <span>
                {report.summary.counts?.PASSED || 0} 通过 /{" "}
                {report.results?.length || 0} 个测试
              </span>
              <div className="spacer" />
              {!terminal(report.status) && permissions.execute && (
                <button
                  disabled={report.cancel_requested}
                  onClick={() =>
                    act(async () =>
                      setReport(
                        await api("/runs/" + report.id + "/cancel", {
                          method: "POST",
                        }),
                      ),
                    )
                  }
                >
                  <X size={14} />
                  {report.cancel_requested ? "正在取消…" : "取消任务"}
                </button>
              )}
              {["json", "junit"].map((f) => (
                <button
                  key={f}
                  onClick={() =>
                    act(() =>
                      download(
                        "/runs/" + report.id + "/export?format=" + f,
                        report.id + (f === "json" ? ".json" : ".xml"),
                      ),
                    )
                  }
                >
                  <ArrowDownToLine size={15} />
                  {f.toUpperCase()}
                </button>
              ))}
            </div>
            <div className="report-body">
              {report.results?.map((result) => (
                <details
                  key={result.id}
                  className="result-details"
                  open={["FAILED", "ERROR"].includes(result.status)}
                >
                  <summary>
                    <Badge status={result.status} />
                    <strong>{result.name}</strong>
                    <span className="muted">
                      {result.adapter} · {result.duration.toFixed(2)}s
                    </span>
                  </summary>
                  {result.detail.error && (
                    <pre className="error-text">{result.detail.error}</pre>
                  )}
                  {result.detail.attempts?.map((attempt: any, i: number) => (
                    <div className="attempt" key={i}>
                      <h4>
                        尝试 {attempt.attempt} · {attempt.status}
                      </h4>
                      {attempt.error && (
                        <pre className="error-text">{attempt.error}</pre>
                      )}
                      {attempt.steps.map((s: any) => (
                        <div className="step-result" key={s.id}>
                          <div>
                            <Badge status={s.status} />
                            <strong>{s.action}</strong>
                            <code>{s.id}</code>
                          </div>
                          {s.assertions?.map((a: any, j: number) => (
                            <div
                              className={
                                "assertion " +
                                (a.passed ? "" : "assertion-failed")
                              }
                              key={j}
                            >
                              <span>{a.passed ? "✓" : "✕"}</span>
                              <code>
                                {a.path} {a.op} {pretty(a.expected)}
                              </code>
                              <span>
                                实际：{pretty(a.actual)}
                                {a.error && " · " + a.error}
                              </span>
                            </div>
                          ))}
                          <details>
                            <summary>步骤输出</summary>
                            <pre>{pretty(s.output || s.error)}</pre>
                          </details>
                        </div>
                      ))}
                      {attempt.assertions?.length > 0 && (
                        <>
                          <h4>用例断言</h4>
                          <pre>{pretty(attempt.assertions)}</pre>
                        </>
                      )}
                      {attempt.preconditions?.length > 0 && (
                        <>
                          <h4>前置条件</h4>
                          <pre>{pretty(attempt.preconditions)}</pre>
                        </>
                      )}
                      <details>
                        <summary>执行日志</summary>
                        <pre>{attempt.logs.join("\n") || "无日志"}</pre>
                      </details>
                      <div className="evidence-links">
                        {attempt.evidence.map((path: string) => (
                          <button
                            key={path}
                            onClick={() =>
                              act(() =>
                                download(
                                  "/evidence/" +
                                    report.id +
                                    "/" +
                                    result.id +
                                    "/" +
                                    path,
                                  path.split("/").pop()!,
                                ),
                              )
                            }
                          >
                            <ArrowDownToLine size={14} />
                            {path}
                          </button>
                        ))}
                      </div>
                    </div>
                  ))}
                  <details className="result-env">
                    <summary>运行环境与参数</summary>
                    <pre>
                      {pretty({
                        environment: result.detail.environment,
                        parameters: result.detail.parameters,
                        selection: result.detail.selection,
                      })}
                    </pre>
                  </details>
                </details>
              ))}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
