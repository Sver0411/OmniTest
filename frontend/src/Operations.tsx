import { useEffect, useState } from "react";

type Props = {
  api: (path: string) => Promise<any>;
  onError: (message: string) => void;
};

export default function Operations({ api, onError }: Props) {
  const [state, setState] = useState<any>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [nodes, setNodes] = useState<any[]>([]);
  async function refresh() {
    setBusy(true);
    try {
      const [operations, audit, nextNodes] = await Promise.all([
        api("/operations"),
        api("/audit?limit=50"),
        api("/nodes"),
      ]);
      setState(operations);
      setEvents(audit);
      setNodes(nextNodes);
    } catch (error) {
      onError(String(error));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    refresh();
  }, []);
  return (
    <div className="operations-layout">
      <section className="panel settings-card">
        <div className="panel-heading">
          <h2>执行服务</h2>
          <button onClick={refresh} disabled={busy}>
            刷新状态
          </button>
        </div>
        {state && (
          <dl>
            <dt>平台版本</dt>
            <dd>{state.version}</dd>
            <dt>本机活动任务</dt>
            <dd>{state.worker.active_runs}</dd>
            <dt>等待执行</dt>
            <dd>{state.runs.PENDING || 0}</dd>
            <dt>最早等待时间</dt>
            <dd>
              {state.oldest_pending_seconds === null
                ? "—"
                : state.oldest_pending_seconds.toFixed(1) + " 秒"}
            </dd>
            <dt>本机用例并发上限</dt>
            <dd>{state.worker.max_concurrency}</dd>
            <dt>审计记录数</dt>
            <dd>{state.audit_events}</dd>
            <dt>插件加载错误</dt>
            <dd>{state.plugin_errors.length}</dd>
          </dl>
        )}
      </section>
      <section className="panel">
        <div className="panel-heading">
          <h2>远程执行节点</h2>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>节点</th>
                <th>状态</th>
                <th>系统 / 版本</th>
                <th>已安装适配器</th>
                <th>当前任务</th>
                <th>最近连接</th>
              </tr>
            </thead>
            <tbody>
              {nodes.map((node) => (
                <tr key={node.id}>
                  <td>{node.id}</td>
                  <td>
                    {
                      (
                        {
                          online: "在线",
                          offline: "离线",
                          disabled: "已停用",
                        } as Record<string, string>
                      )[node.status]
                    }
                  </td>
                  <td>
                    {node.inventory
                      ? `${node.inventory.os} / ${node.inventory.version}`
                      : "—"}
                  </td>
                  <td>
                    {node.inventory
                      ? Object.keys(node.inventory.adapters).join(", ")
                      : "—"}
                  </td>
                  <td title={node.active_run}>
                    {node.active_run?.slice(0, 8) || "—"}
                  </td>
                  <td>
                    {node.last_seen
                      ? new Date(node.last_seen * 1000).toLocaleString("zh-CN")
                      : "尚未连接"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!nodes.length && <div className="empty">尚未配置远程节点</div>}
      </section>
      <section className="panel">
        <div className="panel-heading">
          <h2>最近操作</h2>
          <span>最近 50 条</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>操作者</th>
                <th>请求</th>
                <th>结果</th>
                <th>请求 ID</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.id}>
                  <td>
                    {new Date(event.created_at * 1000).toLocaleString("zh-CN")}
                  </td>
                  <td>
                    {event.actor} · {event.role}
                  </td>
                  <td>
                    <span
                      className="audit-request"
                      title={`${event.method} ${event.path}`}
                    >
                      {event.method} {event.path}
                    </span>
                  </td>
                  <td>{event.status_code}</td>
                  <td title={event.request_id}>
                    {event.request_id.slice(0, 12)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!events.length && <div className="empty">暂无操作记录</div>}
      </section>
    </div>
  );
}
