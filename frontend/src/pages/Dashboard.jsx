import { useCallback, useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { api } from "../api.js";
import { Badge, PRIORITY, StageTrack, fmtMins, fmtTime, useToast } from "../ui.jsx";
import { ConnectPanel, SetupPanel, TransferInbox } from "../parts.jsx";

const FILTERS = [["", "All"], ["new", "Unassigned"], ["assigned", "Assigned"], ["picked", "Picked"], ["working", "Working"], ["completed", "Completed"], ["transfer", "With admin"]];

export default function Dashboard() {
  const { bump } = useOutletContext();
  const toast = useToast();
  const [data, setData] = useState(null);
  const [caps, setCaps] = useState([]);
  const [transfers, setTransfers] = useState([]);
  const [filter, setFilter] = useState("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    try {
      const [d, c, t] = await Promise.all([api("/dashboard/"), api(`/captures/?status=${filter}&q=${encodeURIComponent(q)}`), api("/transfers/")]);
      setData(d); setCaps(c); setTransfers(t);
    } catch (e) { toast(e.message, "error"); }
  }, [filter, q, toast]);

  useEffect(() => { load(); }, [load, bump]);
  useEffect(() => { const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);

  const run = async (key, path, okMsg) => {
    setBusy(key);
    try {
      const r = await api(path, { method: "POST" });
      toast(okMsg(r), "ok"); load();
    } catch (e) { toast(e.message, "error"); } finally { setBusy(""); }
  };

  const s = data?.stats;
  return (
    <div className="page">
      <div className="page-h">
        <div>
          <h1>Who is on what</h1>
          <p className="muted">Work day {data?.day || "…"}. Live view of captures, splits and the people carrying them. The day closes automatically after the night shift: Excel keeps the record, this app starts clean.</p>
        </div>
        <div className="actions">
          <button className="btn" disabled={busy === "sync"} onClick={() => run("sync", "/sheet/sync/", (r) => `${r.new} new capture(s) read from the sheet`)}>
            {busy === "sync" ? "Reading sheet…" : "Read sheet"}
          </button>
          <button className="btn" disabled={busy === "reset"} onClick={() => window.confirm("Clear today's projects, captures, roster and resource accounts and start a fresh day?") && run("reset", "/reset-day/", (r) => `New day started. Cleared ${r.captures} capture(s).`)}>
            Start new day
          </button>
          <button className="btn primary" disabled={busy === "dist"} onClick={() => run("dist", "/distribute/", (r) => `${r.assigned} capture(s) assigned`)}>
            {busy === "dist" ? "Assigning…" : "Auto-assign waiting captures"}
          </button>
        </div>
      </div>

      <ConnectPanel />
      <SetupPanel onDone={load} sync={data?.sync} />
      <TransferInbox items={transfers} onDone={load} />

      {s && (
        <div className="stats">
          <Stat n={s.unassigned} label="Waiting for a person" tone={s.unassigned ? "warn" : ""} />
          <Stat n={s.in_progress} label="In progress" />
          <Stat n={s.queued_splits} label="Splits in shared queue" tone={s.queued_splits ? "warn" : ""} />
          <Stat n={s.completed_today} label="Completed today" tone="ok" />
          <Stat n={s.on_duty} label="Resources on duty" />
          <Stat n={s.transfers_open} label="Sent back to admin" tone={s.transfers_open ? "warn" : ""} />
          <Stat n={s.wrapups_pending} label="Waiting for remarks" tone={s.wrapups_pending ? "warn" : ""} />
        </div>
      )}

      <h2>Team board</h2>
      {!data && <p className="muted">Loading…</p>}
      {data && data.resources.length === 0 && <div className="empty">No resources yet. Add people under Resources.</div>}
      <div className="board">
        {data?.resources.map((r) => (
          <article key={r.id} className={`person ${r.on_duty ? "" : "off"}`}>
            <header>
              <div className="avatar sm">{r.name[0]}</div>
              <div><strong>{r.name}</strong><span className="muted">{r.email}</span></div>
              <span className={`duty ${r.on_duty ? "on" : ""}`}>{r.on_duty ? "On duty" : "Off today"}</span>
              {r.senior_for && <span className="chip" title={r.senior_for}>Senior</span>}
              {r.splits_today > 0 && <span className="chip">{r.splits_today} splits today</span>}
            </header>
            {r.captures.length === 0 && r.helper_splits.length === 0 && <p className="muted idle">Nothing assigned. {r.completed_today ? `${r.completed_today} completed today.` : ""}</p>}
            {r.captures.map((c) => (
              <div key={c.id} className="work">
                <div className="work-h">
                  <div><strong>{c.name}</strong><span className="muted"> {c.project_name}</span></div>
                  {c.total_splits != null && <span className="chip">{c.splits_done}/{c.total_splits} splits</span>}
                </div>
                <StageTrack capture={c} />
              </div>
            ))}
            {r.helper_splits.length > 0 && (
              <div className="work helper">
                <div className="work-h"><strong>Helping on</strong></div>
                <p className="muted">{r.helper_splits.length} split(s): {[...new Set(r.helper_splits.map((x) => `${x.capture_name}`))].join(", ")}</p>
              </div>
            )}
          </article>
        ))}
      </div>

      <h2>Capture log</h2>
      <div className="toolbar">
        <div className="seg" role="tablist">
          {FILTERS.map(([k, l]) => <button key={k} className={filter === k ? "on" : ""} onClick={() => setFilter(k)}>{l}</button>)}
        </div>
        <input className="search" placeholder="Search project or capture" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Capture</th><th>Project</th><th>Person</th><th>Stage</th><th>Splits</th><th>Picked</th><th>Completed</th><th>Time taken</th></tr></thead>
          <tbody>
            {caps.map((c) => (
              <tr key={c.id}>
                <td data-l="Capture"><strong>{c.name}</strong> <small className={`pri p${c.priority}`}>{PRIORITY[c.priority]}</small>
                  {(c.captured_on || c.capture_path) && <small className="muted path" title={c.capture_path}>{c.captured_on}{c.captured_on && c.capture_path ? " · " : ""}{c.capture_path}</small>}</td>
                <td data-l="Project">{c.project_name}</td>
                <td data-l="Person">{c.owner_name || "—"}{c.helpers.length > 0 && <small className="muted"> + {c.helpers.join(", ")}</small>}</td>
                <td data-l="Stage"><Badge status={c.status}>{c.status_label}</Badge></td>
                <td data-l="Splits">{c.total_splits != null ? `${c.splits_done}/${c.total_splits}` : "—"}</td>
                <td data-l="Picked">{fmtTime(c.picked_at)}</td>
                <td data-l="Completed">{fmtTime(c.completed_at)}</td>
                <td data-l="Time taken">{fmtMins(c.minutes_taken)}</td>
              </tr>
            ))}
            {caps.length === 0 && <tr><td colSpan="8" className="muted center">No captures match. Try “Read sheet” to pull new rows.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const Stat = ({ n, label, tone = "" }) => (
  <div className={`stat ${tone}`}><strong>{n}</strong><span>{label}</span></div>
);
