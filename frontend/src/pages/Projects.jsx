import { useCallback, useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { api } from "../api.js";
import { Badge, PRIORITY, fmtTime, useToast } from "../ui.jsx";

export default function Projects() {
  const { bump } = useOutletContext();
  const toast = useToast();
  const [projects, setProjects] = useState([]);
  const [people, setPeople] = useState([]);
  const [open, setOpen] = useState({});

  const load = useCallback(async () => {
    try {
      const [p, r] = await Promise.all([api("/projects/"), api("/resources/")]);
      setProjects(p); setPeople(r.filter((x) => x.is_active));
    } catch (e) { toast(e.message, "error"); }
  }, [toast]);
  useEffect(() => { load(); }, [load, bump]);

  const assign = async (capId, userId) => {
    if (!userId) return;
    try { await api(`/captures/${capId}/assign/`, { method: "POST", body: { user_id: Number(userId) } }); toast("Assigned. The person has been notified.", "ok"); load(); }
    catch (e) { toast(e.message, "error"); }
  };

  return (
    <div className="page">
      <div className="page-h"><div><h1>Projects</h1><p className="muted">Everything read from the sheet. Assign or reassign a capture by hand; the person gets a “you got a task” notification.</p></div></div>
      {projects.length === 0 && <div className="empty">No projects yet. Use “Read sheet” on the dashboard.</div>}
      {projects.map((p) => {
        const isOpen = open[p.id] ?? true;
        const pct = p.total ? Math.round((p.completed / p.total) * 100) : 0;
        return (
          <section key={p.id} className="panel project">
            <button className="proj-h" onClick={() => setOpen({ ...open, [p.id]: !isOpen })} aria-expanded={isOpen}>
              <strong>{p.name}</strong>
              <span className="muted">{p.completed}/{p.total} captures completed</span>
              <span className="bar"><i style={{ width: `${pct}%` }} /></span>
            </button>
            {isOpen && (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Capture</th><th>Priority</th><th>Stage</th><th>Person</th><th>Picked</th><th>Assign to</th></tr></thead>
                  <tbody>
                    {p.captures.map((c) => (
                      <tr key={c.id}>
                        <td data-l="Capture"><strong>{c.name}</strong></td>
                        <td data-l="Priority"><small className={`pri p${c.priority}`}>{PRIORITY[c.priority]}</small></td>
                        <td data-l="Stage"><Badge status={c.status}>{c.status_label}</Badge></td>
                        <td data-l="Person">{c.owner_name || "—"}</td>
                        <td data-l="Picked">{fmtTime(c.picked_at)}</td>
                        <td data-l="Assign to">
                          {["working", "completed"].includes(c.status) ? <span className="muted">Locked</span> : (
                            <select value="" onChange={(e) => assign(c.id, e.target.value)} aria-label={`Assign ${c.name}`}>
                              <option value="">{c.owner_name ? "Reassign…" : "Choose person…"}</option>
                              {people.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                            </select>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
