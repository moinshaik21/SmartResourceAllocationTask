import { useCallback, useEffect, useRef, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { api, savedUser } from "../api.js";
import { Badge, Modal, PRIORITY, StageTrack, useToast } from "../ui.jsx";
import { TransferDialog, WrapUpDialog } from "../parts.jsx";

export default function Home() {
  const { bump } = useOutletContext();
  const toast = useToast();
  const user = savedUser();
  const [work, setWork] = useState(null);
  const [busy, setBusy] = useState("");
  const [splitFor, setSplitFor] = useState(null);   // capture being split
  const [total, setTotal] = useState("");
  const [decision, setDecision] = useState(null);   // { total, limit, overflow }
  const [transferFor, setTransferFor] = useState(null);
  const [wrap, setWrap] = useState(null);           // capture waiting for pipeline remarks + sub-links
  const asked = useRef(new Set());

  const load = useCallback(() => api("/me/work/").then(setWork).catch((e) => toast(e.message, "error")), [toast]);
  useEffect(() => { load(); }, [load, bump]);

  const act = async (key, fn, ok) => {
    setBusy(key);
    try { await fn(); if (ok) toast(ok, "ok"); await load(); }
    catch (e) { toast(e.message, "error"); } finally { setBusy(""); }
  };

  const needTask = () => act("task", () => api("/me/request-task/", { method: "POST" }), "Task added to your list.");
  const pick = (c) => act(`pick${c.id}`, () => api(`/captures/${c.id}/pick/`, { method: "POST" }), `${c.name} picked.`);
  const splitAct = (id, action) => act(`s${id}`, () => api(`/splits/${id}/${action}/`, { method: "POST" }), action === "done" ? "Split finished." : null);

  const submitSplits = async (continue_all) => {
    const n = Number(decision?.total ?? total);
    setBusy("splits");
    try {
      const r = await api(`/captures/${splitFor.id}/splits/`, { method: "POST", body: { total: n, ...(continue_all === undefined ? {} : { continue_all }) } });
      if (r.needs_decision) { setDecision({ total: n, limit: r.limit, overflow: r.overflow }); return; }
      setSplitFor(null); setDecision(null); setTotal("");
      toast(r.handed_over || r.queued ? `${r.kept} splits are yours. ${r.handed_over} handed to a teammate${r.queued ? `, ${r.queued} waiting in the queue` : ""}.` : `${r.kept} splits ready to work.`, "ok");
      load();
    } catch (e) { toast(e.message, "error"); } finally { setBusy(""); }
  };

  // after the last split is done, ask once for the pipeline remark and sub-links
  useEffect(() => {
    const next = work?.wrapup_pending?.find((c) => !asked.current.has(c.id));
    if (next && !wrap) { asked.current.add(next.id); setWrap(next); }
  }, [work, wrap]);

  const closeSplit = () => { setSplitFor(null); setDecision(null); setTotal(""); };
  const empty = work && work.captures.length === 0 && work.split_tasks.length === 0 && work.wrapup_pending.length === 0;

  return (
    <div className="page narrow">
      <div className="page-h">
        <div><h1>Hi {user.name.split(" ")[0]}</h1>
          <p className="muted">{work ? `${work.completed_today} capture(s) completed today.` : "Loading your work…"}</p></div>
      </div>

      {empty && (
        <div className="empty-hero">
          <h2>You have no work right now</h2>
          <p className="muted">Ask for the next task. You'll get overflow splits from teammates first, then the highest-priority capture waiting.</p>
          <button className="btn primary big" onClick={needTask} disabled={busy === "task"}>{busy === "task" ? "Finding a task…" : "I need a task"}</button>
        </div>
      )}

      {work?.captures.map((c) => (
        <section key={c.id} className="panel capture">
          <div className="work-h">
            <div><h2>{c.name}</h2><span className="muted">{c.project_name}</span>{c.capture_path && <small className="muted path" title={c.capture_path}>{c.captured_on ? c.captured_on + " · " : ""}{c.capture_path}</small>}</div>
            <div className="right"><small className={`pri p${c.priority}`}>{PRIORITY[c.priority]}</small>
              {c.total_splits != null && <span className="chip">{c.splits_done}/{c.total_splits} splits</span>}</div>
          </div>
          <StageTrack capture={c} />

          {c.status === "assigned" && (
            <div className="row-btns">
              <button className="btn primary" disabled={busy === `pick${c.id}`} onClick={() => pick(c)}>Pick this capture</button>
              <button className="btn" onClick={() => setTransferFor(c)}>I can't take this</button>
            </div>
          )}
          {c.status === "picked" && (
            <div className="callout">
              <p>Count the splits in this capture, then enter the number. If it is more than {work.limit}, you can keep them all or hand the rest over.</p>
              <div className="row-btns">
                <button className="btn primary" onClick={() => setSplitFor(c)}>Enter number of splits</button>
                <button className="btn" onClick={() => setTransferFor(c)}>I can't take this</button>
              </div>
            </div>
          )}
          {c.status === "working" && (
            <div className="splits">
              {c.splits.map((s) => {
                const mine = s.assignee === user.id;
                return (
                  <div key={s.id} className={`split ${s.status}`}>
                    <strong>Split {s.number}</strong>
                    <span className="muted">{mine ? "You" : s.assignee_name || "In queue"}</span>
                    {mine && s.status === "pending" && <button className="btn sm" onClick={() => splitAct(s.id, "start")}>Start</button>}
                    {mine && s.status === "working" && <button className="btn sm primary" onClick={() => splitAct(s.id, "done")}>Done</button>}
                    {s.status === "done" && <Badge status="completed">Done</Badge>}
                    {!mine && s.status !== "done" && <Badge status={s.status === "working" ? "working" : "assigned"}>{s.status === "working" ? "Working" : "Waiting"}</Badge>}
                  </div>
                );
              })}
            </div>
          )}
        </section>
      ))}

      {work?.wrapup_pending.map((c) => (
        <section key={c.id} className="panel finish">
          <div className="work-h">
            <div><h2>{c.name}</h2><span className="muted">{c.project_name} · all {c.total_splits} splits done</span></div>
            <button className="btn primary" onClick={() => setWrap(c)}>Add pipeline and remarks</button>
          </div>
          <p className="muted">One last step: tell us which pipeline you applied, and whether any split has sub-links.</p>
        </section>
      ))}

      {work?.split_tasks.length > 0 && (
        <section className="panel">
          <h2>Splits handed to you</h2>
          <div className="splits">
            {work.split_tasks.map((s) => (
              <div key={s.id} className={`split ${s.status}`}>
                <strong>{s.capture_name} · Split {s.number}</strong>
                <span className="muted">{s.project_name} · from {s.from_name}</span>
                {s.status === "pending" && <button className="btn sm" onClick={() => splitAct(s.id, "start")}>Start</button>}
                {s.status === "working" && <button className="btn sm primary" onClick={() => splitAct(s.id, "done")}>Done</button>}
              </div>
            ))}
          </div>
        </section>
      )}

      {transferFor && <TransferDialog capture={transferFor} onClose={() => setTransferFor(null)} onDone={() => { setTransferFor(null); load(); }} />}
      {wrap && <WrapUpDialog capture={wrap} onClose={() => setWrap(null)} onDone={() => { setWrap(null); load(); }} />}

      {splitFor && (
        <Modal title={decision ? `${decision.total} splits is more than ${decision.limit}` : `Splits in ${splitFor.name}`} onClose={closeSplit}>
          {!decision ? (
            <form onSubmit={(e) => { e.preventDefault(); submitSplits(); }}>
              <label className="field">How many splits does this capture have?
                <input type="number" min="1" max="500" autoFocus required value={total} onChange={(e) => setTotal(e.target.value)} />
              </label>
              <div className="modal-actions">
                <button type="button" className="btn" onClick={closeSplit}>Cancel</button>
                <button className="btn primary" disabled={busy === "splits"}>Continue</button>
              </div>
            </form>
          ) : (
            <>
              <p>One person can take up to {decision.limit} splits. The other <strong>{decision.overflow}</strong> can stay with you or go to a teammate who is working today.</p>
              <div className="modal-actions stack">
                <button className="btn primary" disabled={busy === "splits"} onClick={() => submitSplits(true)}>Keep all {decision.total} splits</button>
                <button className="btn" disabled={busy === "splits"} onClick={() => submitSplits(false)}>Keep {decision.limit}, hand over {decision.overflow}</button>
              </div>
            </>
          )}
        </Modal>
      )}
    </div>
  );
}
