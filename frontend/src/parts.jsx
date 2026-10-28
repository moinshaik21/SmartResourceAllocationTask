import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { Modal, useToast } from "./ui.jsx";

/* ---------- admin: today's setup (start row, resources from Excel, allocation mode) ---------- */
function ago(iso) {
  if (!iso) return "not yet";
  const sec = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return sec < 60 ? `${sec}s ago` : sec < 3600 ? `${Math.round(sec / 60)} min ago` : `${Math.round(sec / 3600)} h ago`;
}

/* ---------- admin: one-time Microsoft sign-in (device code). The token is kept in the database. ---------- */
export function ConnectPanel() {
  const toast = useToast();
  const [st, setSt] = useState(null);
  const [flow, setFlow] = useState(null);   // { user_code, verification_uri }
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api("/ms/status/").then(setSt).catch(() => {}), []);

  useEffect(() => {
    load();
    const t = setInterval(load, 60000);                       // notice an expired sign-in while the page stays open
    window.addEventListener("ms-expired", load);
    return () => { clearInterval(t); window.removeEventListener("ms-expired", load); };
  }, [load]);

  useEffect(() => {                                           // while the admin signs in, check every 3 seconds
    if (!flow) return undefined;
    const t = setInterval(async () => {
      const s = await api("/ms/status/").catch(() => null);
      if (!s) return;
      setSt(s);
      if (s.state === "connected") { setFlow(null); toast("Microsoft is connected.", "ok"); }
      else if (s.flow === "failed") { setFlow(null); toast(s.error || "The sign-in did not finish.", "error"); }
    }, 3000);
    return () => clearInterval(t);
  }, [flow, toast]);

  if (!st || st.mode === "app") return null;

  const start = async () => {
    setBusy(true);
    try { setFlow(await api("/ms/connect/", { method: "POST" })); }
    catch (e) { toast(e.message, "error"); } finally { setBusy(false); }
  };
  const disconnect = async () => {
    if (!window.confirm("Disconnect Microsoft? Nothing will be read from or written to Excel until you sign in again.")) return;
    setSt(await api("/ms/disconnect/", { method: "POST" }));
  };

  if (st.state === "connected" && !flow) {
    return (
      <p className="ms-ok">
        <span className="dot" /> Microsoft connected{st.account ? ` as ${st.account}` : ""}.
        <button className="linkbtn" onClick={disconnect}>Disconnect</button>
      </p>
    );
  }
  const expired = st.state === "expired";
  return (
    <section className="panel connect">
      <h2>{expired ? "Microsoft sign-in expired" : "Connect to Microsoft Excel"}</h2>
      {!flow ? (
        <>
          <p className="muted">
            {expired
              ? "Excel stopped answering because the saved sign-in is no longer valid. Sign in again - it takes a minute."
              : "Sign in once with your Microsoft work account. The workbooks are then read and written as you, and the sign-in is remembered."}
          </p>
          {st.error && expired && <p className="small-note muted">{st.error}</p>}
          <button className="btn primary" disabled={busy} onClick={start}>{busy ? "Starting…" : "Sign in with Microsoft"}</button>
        </>
      ) : (
        <>
          <ol className="signin">
            <li>Open <a href={flow.verification_uri} target="_blank" rel="noreferrer">{flow.verification_uri.replace("https://", "")}</a></li>
            <li>Enter this code: <strong className="code">{flow.user_code}</strong></li>
            <li>Sign in with your work account and accept. This page continues by itself.</li>
          </ol>
          <p className="muted small-note">Waiting for you to finish… the code works for about {Math.round(flow.expires_in / 60)} minutes.</p>
          <button className="btn" onClick={() => setFlow(null)}>Cancel</button>
        </>
      )}
    </section>
  );
}

export function SetupPanel({ onDone, sync }) {
  const toast = useToast();
  const [cfg, setCfg] = useState({ captures_url: "", captures_tab: "", start_row: "", end_row: "" });
  const [mode, setMode] = useState("manual");
  const [busy, setBusy] = useState("");
  const [last, setLast] = useState(null);

  useEffect(() => {
    api("/sheet/config/").then((c) => setCfg({
      captures_url: c.captures_url || "", captures_tab: c.captures_tab || "",
      start_row: c.start_row ?? "", end_row: c.end_row ?? "",
    })).catch(() => {});
  }, []);
  const set = (k) => (e) => setCfg({ ...cfg, [k]: e.target.value });

  const start = async () => {
    if (!cfg.start_row) return toast("Type the row number to start from.", "error");
    setBusy("start");
    try {
      const r = await api("/shift/start/", { method: "POST", body: {
        ...cfg, start_row: Number(cfg.start_row), end_row: cfg.end_row ? Number(cfg.end_row) : "", mode } });
      setLast(r);
      toast(`${r.on_duty?.length ?? 0} on duty · ${r.sync.new} new capture(s)${mode === "auto" ? ` · ${r.assigned} allocated` : ""}`, "ok");
      onDone();
    } catch (e) { toast(e.message, "error"); } finally { setBusy(""); }
  };

  const fetchOnly = async () => {
    setBusy("fetch");
    try {
      const r = await api("/roster/fetch/", { method: "POST" });
      setLast({ on_duty: r.on_duty, unknown: r.unknown });
      toast(`${r.on_duty.length} people fetched from the Resources tab.`, "ok"); onDone();
    } catch (e) { toast(e.message, "error"); } finally { setBusy(""); }
  };

  return (
    <section className="panel setup">
      <h2>Start today's shift</h2>
      <p className="muted">Type the row to start from. The app reads new captures from that row down, fetches everyone with <code>Y</code> in <em>Working Today</em> on the Resources tab, and allocates.</p>
      <div className="setup-grid">
        <label className="field">Start from row<input type="number" min="2" value={cfg.start_row} onChange={set("start_row")} placeholder="e.g. 25560" /></label>
        <label className="field">Last row (optional)<input type="number" min="2" value={cfg.end_row} onChange={set("end_row")} placeholder="to the end" /></label>
        <label className="field">Tab name<input value={cfg.captures_tab} onChange={set("captures_tab")} placeholder="Detail Visual projects" /></label>
        <label className="field wide2">This month's production workbook link<input value={cfg.captures_url} onChange={set("captures_url")} placeholder="leave empty to use the saved link" /></label>
      </div>
      <div className="mode">
        <label className={mode === "auto" ? "on" : ""}><input type="radio" name="mode" checked={mode === "auto"} onChange={() => setMode("auto")} />
          <span><strong>Automatic</strong><small>One capture per person, project by project, lightest load first</small></span></label>
        <label className={mode === "manual" ? "on" : ""}><input type="radio" name="mode" checked={mode === "manual"} onChange={() => setMode("manual")} />
          <span><strong>Manual</strong><small>I will assign each capture on the Projects page</small></span></label>
      </div>
      <div className="actions">
        <button className="btn primary" disabled={busy === "start"} onClick={start}>{busy === "start" ? "Starting…" : "Start shift"}</button>
        <button className="btn" disabled={busy === "fetch"} onClick={fetchOnly}>{busy === "fetch" ? "Fetching…" : "Only fetch today's people"}</button>
      </div>
      {sync?.start_row && (
        <p className="muted small-note">
          {sync.auto ? "New captures are checked automatically while this page is open." : "Automatic checking is off."}{" "}
          Last check: {ago(sync.last_sync_at)}{sync.last_sync_new ? ` · ${sync.last_sync_new} new` : ""}
          {sync.error && <span className="error"> · {sync.error}</span>}
        </p>
      )}
      {last?.unknown?.length > 0 && <p className="error">Not found in the Resources tab: {last.unknown.join(", ")}</p>}
      {last?.on_duty?.length > 0 && <p className="muted small-note">On duty: {last.on_duty.map((u) => u.name).join(", ")}</p>}
    </section>
  );
}

/* ---------- admin: captures employees sent back ---------- */
export function TransferInbox({ items, onDone }) {
  const toast = useToast();
  const [choice, setChoice] = useState({});
  if (!items?.length) return null;

  const send = async (t) => {
    const id = choice[t.id] || t.seniors[0]?.id;
    if (!id) return toast("Choose who should take it.", "error");
    try {
      await api(`/transfers/${t.id}/resolve/`, { method: "POST", body: { user_id: Number(id) } });
      toast("Sent. They have been notified.", "ok"); onDone();
    } catch (e) { toast(e.message, "error"); }
  };

  return (
    <section className="panel inbox">
      <h2>Sent back to you <span className="chip warn">{items.length}</span></h2>
      {items.map((t) => (
        <div key={t.id} className="inbox-item">
          <div>
            <strong>{t.capture_name}</strong> <span className="muted">{t.project_name}</span>
            <p className="reason"><b>{t.from_name}:</b> {t.reason}</p>
          </div>
          <div className="send">
            <select value={choice[t.id] ?? t.seniors[0]?.id ?? ""} onChange={(e) => setChoice({ ...choice, [t.id]: e.target.value })}>
              <option value="" disabled>Choose a person…</option>
              {t.seniors.length > 0 && <optgroup label="Seniors for this project">
                {t.seniors.map((u) => <option key={u.id} value={u.id}>{u.name}{u.on_duty ? "" : " (not on duty)"}</option>)}
              </optgroup>}
              <optgroup label="Others on duty today">
                {t.others.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </optgroup>
            </select>
            <button className="btn primary sm" onClick={() => send(t)}>Send</button>
          </div>
          {t.seniors.length === 0 && <small className="muted">No senior is listed for this project. Add the project under “Senior for” on the Resources page.</small>}
        </div>
      ))}
    </section>
  );
}

/* ---------- employee: cannot take this capture ---------- */
export function TransferDialog({ capture, onClose, onDone }) {
  const toast = useToast();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    if (!reason.trim()) return toast("Write a short reason for the admin.", "error");
    setBusy(true);
    try {
      await api(`/captures/${capture.id}/transfer/`, { method: "POST", body: { reason } });
      toast("Sent to the admin.", "ok"); onDone();
    } catch (err) { toast(err.message, "error"); } finally { setBusy(false); }
  };
  return (
    <Modal title={`Send ${capture.name} back to the admin`} onClose={onClose}>
      <form onSubmit={submit}>
        <p className="muted">The admin will give it to a senior for this project. Nothing is written in Excel.</p>
        <label className="field">Why can't you take it?
          <textarea rows="3" autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. I have not worked on this project before" />
        </label>
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy}>{busy ? "Sending…" : "Send to admin"}</button>
        </div>
      </form>
    </Modal>
  );
}

/* ---------- employee: after the last split - pipeline remark, then sub-links ---------- */
export function WrapUpDialog({ capture, onClose, onDone }) {
  const toast = useToast();
  const [pipes, setPipes] = useState(null);
  const [pipeline, setPipeline] = useState("");
  const [remarks, setRemarks] = useState("");
  const [step, setStep] = useState(1);
  const [has, setHas] = useState(null);
  const [counts, setCounts] = useState({});
  const [busy, setBusy] = useState(false);
  const numbers = (capture.splits || []).map((s) => s.number);
  const total = Object.values(counts).reduce((a, b) => a + (Number(b) || 0), 0);

  useEffect(() => { api("/pipelines/").then((r) => setPipes(r.pipelines)).catch(() => setPipes([])); }, []);

  const next = () => {
    if (!pipeline.trim()) return toast("Choose the pipeline that was applied.", "error");
    setStep(2);
  };
  const submit = async () => {
    if (has === null) return toast("Answer yes or no.", "error");
    setBusy(true);
    try {
      await api(`/captures/${capture.id}/wrapup/`, { method: "POST", body: { pipeline, remarks, sublinks: has ? counts : {} } });
      toast(has && total ? `Saved. ${total} sub-link row(s) added.` : "Saved.", "ok"); onDone();
    } catch (e) { toast(e.message, "error"); } finally { setBusy(false); }
  };

  return (
    <Modal title={`${capture.name}: all splits done`} onClose={onClose}>
      <ol className="steps"><li className={step === 1 ? "on" : "past"}>1 · Pipeline</li><li className={step === 2 ? "on" : ""}>2 · Sub-links</li></ol>
      {step === 1 ? (
        <>
          <label className="field">Which pipeline did you apply to these splits?
            {pipes && pipes.length > 0 ? (
              <select autoFocus value={pipeline} onChange={(e) => setPipeline(e.target.value)}>
                <option value="" disabled>Choose a pipeline…</option>
                {pipes.map((p) => <option key={p}>{p}</option>)}
              </select>
            ) : <input autoFocus value={pipeline} onChange={(e) => setPipeline(e.target.value)} placeholder={pipes ? "Type the pipeline name" : "Loading…"} />}
          </label>
          <label className="field">Remarks (optional)
            <textarea rows="2" value={remarks} onChange={(e) => setRemarks(e.target.value)} />
          </label>
          <div className="modal-actions">
            <button className="btn" onClick={onClose}>Later</button>
            <button className="btn primary" onClick={next}>Next</button>
          </div>
        </>
      ) : (
        <>
          <p><strong>Do any of the splits have sub-links?</strong></p>
          <div className="yesno">
            <button className={`btn ${has === true ? "primary" : ""}`} onClick={() => setHas(true)}>Yes, some do</button>
            <button className={`btn ${has === false ? "primary" : ""}`} onClick={() => setHas(false)}>No, only the splits</button>
          </div>
          {has === true && (
            <>
              <p className="muted">How many sub-links does each split have? Leave 0 for none.</p>
              <div className="sublinks">
                {numbers.map((n) => (
                  <label key={n}>Split {n}
                    <input type="number" min="0" max="50" value={counts[n] ?? 0}
                      onChange={(e) => setCounts({ ...counts, [n]: e.target.value })} />
                  </label>
                ))}
              </div>
              <p className="muted small-note">{total} extra row(s) will be added under the splits in Excel, for example <code>sp-1 link-1-1</code>, <code>sp-1 link-1-2</code>.</p>
            </>
          )}
          <div className="modal-actions">
            <button className="btn" onClick={() => setStep(1)}>Back</button>
            <button className="btn primary" disabled={busy} onClick={submit}>{busy ? "Saving…" : "Submit"}</button>
          </div>
        </>
      )}
    </Modal>
  );
}
