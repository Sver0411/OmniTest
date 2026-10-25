import { useState } from "react";

type Item = { id: string; body: Record<string, any> };
type Props = {
  body: string;
  targets: Item[];
  onChange: (body: string) => void;
  onValidity: (valid: boolean) => void;
};

export default function EnvironmentEditor({
  body,
  targets,
  onChange,
  onValidity,
}: Props) {
  const value = JSON.parse(body);
  const [variables, setVariables] = useState(
    JSON.stringify(value.variables || {}, null, 2),
  );
  const [error, setError] = useState("");
  const overrides: [string, string][] = Object.entries(
    value.target_overrides || {},
  );
  function update(patch: any) {
    onChange(JSON.stringify({ ...value, ...patch }, null, 2));
  }
  function mapping(rows: [string, string][]) {
    update({ target_overrides: Object.fromEntries(rows) });
  }
  return (
    <div className="environment-editor">
      <label>
        环境名称
        <input
          aria-label="环境名称"
          value={value.name}
          onChange={(event) => update({ name: event.target.value })}
        />
      </label>
      <label>
        环境变量（JSON）
        <textarea
          aria-label="环境变量 JSON"
          spellCheck={false}
          value={variables}
          onChange={(event) => {
            setVariables(event.target.value);
            try {
              const parsed = JSON.parse(event.target.value);
              if (
                !parsed ||
                Array.isArray(parsed) ||
                typeof parsed !== "object"
              )
                throw new Error();
              update({ variables: parsed });
              setError("");
              onValidity(true);
            } catch {
              setError("环境变量必须为 JSON 对象");
              onValidity(false);
            }
          }}
        />
      </label>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <div className="panel-heading">
        <h3>目标替换</h3>
        <button
          disabled={
            !targets.some(
              (target) => !(target.id in (value.target_overrides || {})),
            )
          }
          onClick={() => {
            const target = targets.find(
              (item) => !(item.id in (value.target_overrides || {})),
            );
            if (target) mapping([...overrides, [target.id, target.id]]);
          }}
        >
          添加替换
        </button>
      </div>
      <p>将用例中的逻辑目标映射到同项目、同类型的实际目标。</p>
      {overrides.map(([source, replacement], index) => (
        <div className="target-mapping" key={source}>
          <select
            aria-label={`逻辑目标 ${index + 1}`}
            value={source}
            onChange={(event) =>
              mapping(
                overrides.map((pair, position) =>
                  position === index
                    ? [event.target.value, event.target.value]
                    : pair,
                ),
              )
            }
          >
            {targets
              .filter(
                (target) =>
                  target.id === source ||
                  !overrides.some(([identity]) => identity === target.id),
              )
              .map((target) => (
                <option key={target.id} value={target.id}>
                  {target.body.name}
                </option>
              ))}
          </select>
          <span>→</span>
          <select
            aria-label={`实际目标 ${index + 1}`}
            value={replacement}
            onChange={(event) =>
              mapping(
                overrides.map((pair, position) =>
                  position === index ? [source, event.target.value] : pair,
                ),
              )
            }
          >
            {targets
              .filter(
                (target) =>
                  target.body.kind ===
                  targets.find((item) => item.id === source)?.body.kind,
              )
              .map((target) => (
                <option key={target.id} value={target.id}>
                  {target.body.name}
                </option>
              ))}
          </select>
          <button
            aria-label={`移除替换 ${index + 1}`}
            onClick={() =>
              mapping(overrides.filter((_, position) => position !== index))
            }
          >
            移除
          </button>
        </div>
      ))}
    </div>
  );
}
