import { useEffect, useState } from "react";

type Item = { id: string; body: Record<string, any> };
type Props = {
  body: string;
  onChange: (body: string) => void;
  suites: Item[];
  environments: Item[];
  nodes: any[];
  api: (path: string) => Promise<any>;
};

export default function ScheduleEditor({
  body,
  onChange,
  suites,
  environments,
  nodes,
  api,
}: Props) {
  const value = JSON.parse(body);
  const [occurrences, setOccurrences] = useState<any[]>([]);
  const [error, setError] = useState("");
  function update(patch: Record<string, any>) {
    onChange(JSON.stringify({ ...value, ...patch }, null, 2));
  }
  const type = value.schedule_type || "interval";
  useEffect(() => {
    let current = true;
    setOccurrences([]);
    const timer = setTimeout(async () => {
      try {
        const params = new URLSearchParams({
          schedule_type: type,
          timezone: value.timezone || "UTC",
        });
        if (type === "cron") params.set("cron", value.cron || "");
        else
          params.set("interval_seconds", String(value.interval_seconds || 0));
        const response = await api("/schedules/preview?" + params);
        if (current) {
          setOccurrences(response.occurrences);
          setError("");
        }
      } catch (error) {
        if (current) setError(String(error));
      }
    }, 300);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [type, value.cron, value.interval_seconds, value.timezone]);
  return (
    <div className="schedule-editor">
      <div className="schedule-fields">
        <label>
          名称
          <input
            aria-label="名称"
            value={value.name}
            onChange={(event) => update({ name: event.target.value })}
          />
        </label>
        <label>
          测试套件
          <select
            aria-label="测试套件"
            value={value.suite_id}
            onChange={(event) => update({ suite_id: event.target.value })}
          >
            <option value="">选择测试套件</option>
            {suites.map((suite) => (
              <option key={suite.id} value={suite.id}>
                {suite.body.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          调度方式
          <select
            aria-label="调度方式"
            value={type}
            onChange={(event) =>
              update({
                schedule_type: event.target.value,
                interval_seconds:
                  event.target.value === "interval" ? 3600 : null,
                cron: event.target.value === "cron" ? "0 9 * * mon-fri" : null,
                next_run: null,
              })
            }
          >
            <option value="interval">固定间隔</option>
            <option value="cron">日历（Cron）</option>
          </select>
        </label>
        {type === "interval" ? (
          <label>
            间隔（秒）
            <input
              type="number"
              min="5"
              max="31536000"
              aria-label="间隔（秒）"
              value={value.interval_seconds ?? ""}
              onChange={(event) =>
                update({
                  interval_seconds: Number(event.target.value),
                  next_run: null,
                })
              }
            />
          </label>
        ) : (
          <label>
            Cron 表达式
            <input
              aria-label="Cron 表达式"
              value={value.cron || ""}
              onChange={(event) =>
                update({ cron: event.target.value, next_run: null })
              }
            />
            <span>分 时 日 月 周；周一为 0，可用 mon-fri。</span>
          </label>
        )}
        <label>
          时区
          <input
            list="schedule-timezones"
            aria-label="时区"
            value={value.timezone || "UTC"}
            onChange={(event) =>
              update({ timezone: event.target.value, next_run: null })
            }
          />
          <datalist id="schedule-timezones">
            {[
              "Asia/Shanghai",
              "UTC",
              "Asia/Tokyo",
              "Europe/Berlin",
              "America/New_York",
            ].map((zone) => (
              <option key={zone} value={zone} />
            ))}
          </datalist>
        </label>
        <label>
          执行节点
          <select
            aria-label="执行节点"
            value={value.node_id || ""}
            onChange={(event) =>
              update({ node_id: event.target.value || null })
            }
          >
            <option value="">本机</option>
            {nodes
              .filter((node) => node.enabled)
              .map((node) => (
                <option key={node.id} value={node.id}>
                  {node.id}
                </option>
              ))}
          </select>
        </label>
        <label>
          执行环境
          <select
            aria-label="执行环境"
            value={value.environment_id || ""}
            onChange={(event) =>
              update({ environment_id: event.target.value || null })
            }
          >
            <option value="">用例默认环境</option>
            {environments.map((environment) => (
              <option key={environment.id} value={environment.id}>
                {environment.body.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          上次任务未结束
          <select
            aria-label="上次任务未结束"
            value={value.overlap || "allow"}
            onChange={(event) => update({ overlap: event.target.value })}
          >
            <option value="skip">跳过本次</option>
            <option value="allow">允许重叠</option>
          </select>
        </label>
        <label>
          错过执行时间
          <select
            aria-label="错过执行时间"
            value={value.misfire_policy || "coalesce"}
            onChange={(event) => update({ misfire_policy: event.target.value })}
          >
            <option value="coalesce">合并为一次补跑</option>
            <option value="skip">超出宽限期则跳过</option>
          </select>
        </label>
        <label>
          补跑宽限期（秒）
          <input
            type="number"
            min="1"
            max="86400"
            aria-label="补跑宽限期（秒）"
            disabled={value.misfire_policy !== "skip"}
            value={value.misfire_grace_seconds ?? 60}
            onChange={(event) =>
              update({ misfire_grace_seconds: Number(event.target.value) })
            }
          />
        </label>
        <label className="schedule-enabled">
          <input
            type="checkbox"
            checked={value.enabled}
            onChange={(event) => update({ enabled: event.target.checked })}
          />
          启用调度
        </label>
      </div>
      <aside className="schedule-preview">
        <h3>接下来 5 次计划</h3>
        {error ? (
          <p className="error-text" role="status">
            {error}
          </p>
        ) : (
          <ol>
            {occurrences.map((occurrence) => (
              <li key={occurrence.timestamp}>
                {occurrence.local.replace("T", " ")}
              </li>
            ))}
          </ol>
        )}
      </aside>
    </div>
  );
}
