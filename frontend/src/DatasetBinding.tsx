import { useState } from "react";

type Props = {
  row: any;
  datasets: any[];
  onClose: () => void;
  onSave: (body: any) => Promise<void>;
};
export default function DatasetBinding({
  row,
  datasets,
  onClose,
  onSave,
}: Props) {
  const [selected, setSelected] = useState<string[]>(
    row.body.dataset_ids || [],
  );
  const [mode, setMode] = useState(selected.length ? "external" : "inline");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setBusy(true);
    try {
      await onSave({
        ...row.body,
        dataset_ids: mode === "external" ? selected : [],
        datasets: mode === "external" ? [] : row.body.datasets,
      });
    } catch (error) {
      setError(String(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="modal-backdrop">
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label="用例数据集"
      >
        <div className="modal-heading">
          <h2>{row.body.name} · 数据集</h2>
          <button onClick={onClose}>关闭</button>
        </div>
        <div className="environment-editor">
          <label>
            数据来源
            <select
              aria-label="数据来源"
              value={mode}
              onChange={(event) => setMode(event.target.value)}
            >
              <option value="inline">
                内联数据（{row.body.datasets.length} 行）
              </option>
              <option value="external">项目数据集</option>
            </select>
          </label>
          {mode === "external" && (
            <>
              <p>
                关联后使用数据集当前行，替换用例的内联数据；提交任务后保留独立快照。
              </p>
              {datasets.map((dataset) => (
                <label className="dataset-choice" key={dataset.id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(dataset.id)}
                    onChange={(event) =>
                      setSelected(
                        event.target.checked
                          ? [...selected, dataset.id]
                          : selected.filter((id) => id !== dataset.id),
                      )
                    }
                  />
                  {dataset.body.name} · {dataset.body.rows.length} 行
                </label>
              ))}
            </>
          )}
          {error && (
            <p className="error-text" role="alert">
              {error}
            </p>
          )}
        </div>
        <div className="modal-footer">
          <div className="spacer" />
          <button onClick={onClose}>取消</button>
          <button
            className="primary"
            disabled={busy || (mode === "external" && !selected.length)}
            onClick={save}
          >
            保存关联
          </button>
        </div>
      </section>
    </div>
  );
}
