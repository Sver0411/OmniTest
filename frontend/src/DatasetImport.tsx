import { useEffect, useState } from "react";

type Props = {
  file: File;
  project: string;
  api: (path: string, init?: RequestInit) => Promise<any>;
  onClose: () => void;
  onSaved: () => void;
};

export default function DatasetImport({
  file,
  project,
  api,
  onClose,
  onSaved,
}: Props) {
  const [content, setContent] = useState<string | null>(null);
  const [name, setName] = useState(file.name.replace(/\.[^.]+$/, ""));
  const [types, setTypes] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<any>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const format = file.name.toLowerCase().endsWith(".csv") ? "csv" : "json";
  useEffect(() => {
    let active = true;
    if (file.size > 1_000_000 || !/\.(csv|json)$/i.test(file.name)) {
      setError("请选择不超过 1 MB 的 CSV 或 JSON 文件");
      return;
    }
    file
      .arrayBuffer()
      .then((bytes) => {
        const text = new TextDecoder("utf-8", {
          fatal: true,
          ignoreBOM: true,
        }).decode(bytes);
        if (active) setContent(text);
      })
      .catch(() => {
        if (active) setError("文件必须使用 UTF-8 编码");
      });
    return () => {
      active = false;
    };
  }, [file]);
  const payload = {
    project_id: project,
    filename: file.name,
    format,
    name,
    content,
    column_types: types,
  };
  useEffect(() => {
    let active = true;
    setPreview(null);
    if (content === null) return;
    const timer = setTimeout(
      () =>
        api("/datasets/preview", {
          method: "POST",
          body: JSON.stringify(payload),
        })
          .then((value) => {
            if (active) {
              setPreview(value);
              setError("");
            }
          })
          .catch((error) => {
            if (active) setError(String(error));
          }),
      250,
    );
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [content, name, types]);
  async function save() {
    setBusy(true);
    try {
      await api("/datasets/import", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      onSaved();
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
        aria-label="导入数据集"
      >
        <div className="modal-heading">
          <h2>导入数据集</h2>
          <button onClick={onClose}>关闭</button>
        </div>
        <div className="dataset-import">
          <label>
            名称
            <input
              aria-label="数据集名称"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <p>
            {file.name} ·{" "}
            {preview ? `${preview.row_count} 行，预览前 10 行` : "解析文件中"}
          </p>
          {error && (
            <div className="alert" role="alert">
              {error}
            </div>
          )}
          {preview && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {preview.columns.map((column: string) => (
                      <th key={column}>
                        {column}
                        {format === "csv" && (
                          <select
                            aria-label={`${column} 类型`}
                            value={types[column] || "string"}
                            onChange={(event) =>
                              setTypes({
                                ...types,
                                [column]: event.target.value,
                              })
                            }
                          >
                            {Object.entries({
                              string: "文本",
                              integer: "整数",
                              number: "数字",
                              boolean: "布尔",
                              json: "JSON",
                            }).map(([value, label]) => (
                              <option key={value} value={value}>
                                {label}
                              </option>
                            ))}
                          </select>
                        )}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.rows.map((row: any, index: number) => (
                    <tr key={index}>
                      {preview.columns.map((column: string) => (
                        <td key={column}>
                          {(typeof row[column] === "string"
                            ? row[column]
                            : JSON.stringify(row[column])
                          ).slice(0, 200)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <div className="modal-footer">
          <div className="spacer" />
          <button onClick={onClose}>取消</button>
          <button
            className="primary"
            disabled={busy || !preview || !!error}
            onClick={save}
          >
            确认导入
          </button>
        </div>
      </section>
    </div>
  );
}
