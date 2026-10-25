import { useEffect, useState } from "react";

type Props = {
  api: (path: string, init?: RequestInit) => Promise<any>;
  projects: any[];
};
const labels: Record<string, string> = {
  viewer: "只读",
  operator: "执行",
  editor: "编辑",
};

function Grants({ projects, value, onChange, disabled = false }: any) {
  return (
    <fieldset className="grant-fields" disabled={disabled}>
      <legend>项目权限</legend>
      {projects.map((project: any) => (
        <label key={project.id}>
          {project.body.name}
          <select
            aria-label={`${project.body.name} 权限`}
            value={value[project.id] || ""}
            onChange={(event) => {
              const roles = { ...value };
              if (event.target.value) roles[project.id] = event.target.value;
              else delete roles[project.id];
              onChange(roles);
            }}
          >
            <option value="">无权限</option>
            {Object.entries(labels).map(([role, label]) => (
              <option key={role} value={role}>
                {label}
              </option>
            ))}
          </select>
        </label>
      ))}
    </fieldset>
  );
}

export default function Collaboration({ api, projects }: Props) {
  const [state, setState] = useState<any>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editor, setEditor] = useState<any>(null);
  const [credential, setCredential] = useState<any>(null);
  async function refresh() {
    try {
      setState(await api("/collaboration"));
    } catch (error) {
      setError(String(error));
    }
  }
  useEffect(() => {
    refresh();
  }, []);
  async function act(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
      await refresh();
    } catch (error) {
      setError(String(error));
    } finally {
      setBusy(false);
    }
  }
  function editMember(row?: any) {
    setEditor({
      kind: "members",
      id: row?.id,
      body: {
        username: row?.username || "",
        display_name: row?.display_name || "",
        enabled: row?.enabled ?? true,
        is_admin: row?.is_admin || false,
        project_roles: row?.project_roles || {},
      },
    });
  }
  function editTeam(row?: any) {
    setEditor({
      kind: "teams",
      id: row?.id,
      body: {
        name: row?.name || "",
        description: row?.description || "",
        enabled: row?.enabled ?? true,
        project_roles: row?.project_roles || {},
        member_ids: row?.member_ids || [],
      },
    });
  }
  function update(patch: any) {
    setEditor({ ...editor, body: { ...editor.body, ...patch } });
  }
  async function save() {
    await act(async () => {
      const response = await api(
        `/collaboration/${editor.kind}${editor.id ? "/" + editor.id : ""}`,
        {
          method: editor.id ? "PUT" : "POST",
          body: JSON.stringify(editor.body),
        },
      );
      setEditor(null);
      if (response.token)
        setCredential({
          username: response.member.username,
          token: response.token,
        });
    });
  }
  return (
    <div className="operations-layout">
      {error && (
        <div role="alert" className="alert">
          {error}
        </div>
      )}
      {state && !state.authentication && (
        <div className="inline-info">
          共享账户需要先配置管理员访问凭据并重启服务。
        </div>
      )}
      <section className="panel">
        <div className="panel-heading">
          <h2>成员</h2>
          <button
            className="primary"
            disabled={busy || !state?.authentication}
            onClick={() => editMember()}
          >
            新建成员
          </button>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>用户名</th>
                <th>显示名称</th>
                <th>状态</th>
                <th>授权</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {state?.members.map((member: any) => (
                <tr key={member.id}>
                  <td>{member.username}</td>
                  <td>{member.display_name}</td>
                  <td>{member.enabled ? "启用" : "停用"}</td>
                  <td>
                    {member.is_admin
                      ? "全局管理员"
                      : `${Object.keys(member.project_roles).length} 个直接授权项目`}{" "}
                    ·{" "}
                    {
                      state.teams.filter((team: any) =>
                        team.member_ids.includes(member.id),
                      ).length
                    }{" "}
                    个团队
                  </td>
                  <td>
                    <div className="row-actions">
                      <button
                        disabled={busy}
                        onClick={() => editMember(member)}
                      >
                        编辑成员
                      </button>
                      <button
                        disabled={busy}
                        onClick={() =>
                          act(async () => {
                            const response = await api(
                              `/collaboration/members/${member.id}/rotate`,
                              { method: "POST" },
                            );
                            setCredential({
                              username: response.member.username,
                              token: response.token,
                            });
                          })
                        }
                      >
                        轮换令牌
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {state && !state.members.length && (
          <div className="empty">暂无成员</div>
        )}
      </section>
      <section className="panel">
        <div className="panel-heading">
          <h2>团队</h2>
          <button
            className="primary"
            disabled={busy || !state?.authentication}
            onClick={() => editTeam()}
          >
            新建团队
          </button>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>团队</th>
                <th>状态</th>
                <th>成员数</th>
                <th>授权项目数</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {state?.teams.map((team: any) => (
                <tr key={team.id}>
                  <td>{team.name}</td>
                  <td>{team.enabled ? "启用" : "停用"}</td>
                  <td>{team.member_ids.length}</td>
                  <td>{Object.keys(team.project_roles).length}</td>
                  <td>
                    <button disabled={busy} onClick={() => editTeam(team)}>
                      编辑团队
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {state && !state.teams.length && <div className="empty">暂无团队</div>}
      </section>
      {editor && (
        <div className="modal-backdrop">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label={editor.kind === "members" ? "成员编辑器" : "团队编辑器"}
          >
            <div className="modal-heading">
              <h2>
                {editor.id ? "编辑" : "新建"}
                {editor.kind === "members" ? "成员" : "团队"}
              </h2>
              <button onClick={() => setEditor(null)}>关闭</button>
            </div>
            <div className="environment-editor">
              {error && (
                <div role="alert" className="alert">
                  {error}
                </div>
              )}
              {editor.kind === "members" ? (
                <>
                  <label>
                    用户名
                    <input
                      aria-label="成员用户名"
                      value={editor.body.username}
                      onChange={(event) =>
                        update({ username: event.target.value })
                      }
                    />
                  </label>
                  <label>
                    显示名称
                    <input
                      aria-label="成员显示名称"
                      value={editor.body.display_name}
                      onChange={(event) =>
                        update({ display_name: event.target.value })
                      }
                    />
                  </label>
                  <label className="dataset-choice">
                    <input
                      type="checkbox"
                      checked={editor.body.is_admin}
                      onChange={(event) =>
                        update({ is_admin: event.target.checked })
                      }
                    />
                    全局管理员
                  </label>
                </>
              ) : (
                <>
                  <label>
                    团队名称
                    <input
                      aria-label="团队名称"
                      value={editor.body.name}
                      onChange={(event) => update({ name: event.target.value })}
                    />
                  </label>
                  <label>
                    说明
                    <input
                      aria-label="团队说明"
                      value={editor.body.description}
                      onChange={(event) =>
                        update({ description: event.target.value })
                      }
                    />
                  </label>
                  <fieldset className="grant-fields">
                    <legend>团队成员</legend>
                    {state.members.map((member: any) => (
                      <label className="dataset-choice" key={member.id}>
                        <input
                          type="checkbox"
                          checked={editor.body.member_ids.includes(member.id)}
                          onChange={(event) =>
                            update({
                              member_ids: event.target.checked
                                ? [...editor.body.member_ids, member.id]
                                : editor.body.member_ids.filter(
                                    (id: string) => id !== member.id,
                                  ),
                            })
                          }
                        />
                        {member.display_name} ({member.username})
                      </label>
                    ))}
                  </fieldset>
                </>
              )}
              <Grants
                projects={projects}
                value={editor.body.project_roles}
                onChange={(project_roles: any) => update({ project_roles })}
                disabled={editor.body.is_admin}
              />
              <label className="dataset-choice">
                <input
                  type="checkbox"
                  checked={editor.body.enabled}
                  onChange={(event) =>
                    update({ enabled: event.target.checked })
                  }
                />
                启用
              </label>
            </div>
            <div className="modal-footer">
              <div className="spacer" />
              <button onClick={() => setEditor(null)}>取消</button>
              <button className="primary" disabled={busy} onClick={save}>
                保存
              </button>
            </div>
          </section>
        </div>
      )}
      {credential && (
        <div className="modal-backdrop">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label="成员访问令牌"
          >
            <div className="modal-heading">
              <h2>{credential.username} · 新访问令牌</h2>
            </div>
            <div className="environment-editor">
              <p>
                令牌仅在此显示一次。请妥善保存，用于浏览器会话或
                CLI；轮换后旧令牌立即失效。
              </p>
              <textarea
                aria-label="新成员令牌"
                readOnly
                value={credential.token}
              />
            </div>
            <div className="modal-footer">
              <div className="spacer" />
              <button className="primary" onClick={() => setCredential(null)}>
                已保存，关闭
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
