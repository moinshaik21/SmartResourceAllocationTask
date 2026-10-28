import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { useToast } from "../ui.jsx";

export default function Roster() {
  const toast = useToast();
  const [people, setPeople] = useState([]);
  const [picked, setPicked] = useState(new Set());
  const [lines, setLines] = useState("");
  const [distribute, setDistribute] = useState(true);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  const load = useCallback(async () => {
    try {
      const [all, today] = await Promise.all([api("/resources/"), api("/roster/")]);
      setPeople(all.filter((p) => p.is_active));
      setPicked(new Set(today.map((p) => p.email)));
    } catch (e) { toast(e.message, "error"); }
  }, [toast]);
  useEffect(() => { load(); }, [load]);

  const flip = (email) => setPicked((s) => { const n = new Set(s); n.has(email) ? n.delete(email) : n.add(email); return n; });

  const save = async () => {
    if (!picked.size && !lines.trim()) return toast("Add at least one person who is working today.", "error");
    setBusy(true);
    try {
      const r = await api("/roster/", { method: "POST", body: { emails: [...picked], lines, distribute } });
      setResult(r); setLines("");
      toast(`${r.on_duty.length} on duty${distribute ? `, ${r.assigned} capture(s) assigned and notified` : ""}.`, "ok");
      load();
    } catch (e) { toast(e.message, "error"); } finally { setBusy(false); }
  };

  const fetchFromExcel = async () => {
    setBusy(true);
    try {
      const r = await api("/roster/fetch/", { method: "POST" });
      setResult({ ...r, created: [] });
      toast(`${r.on_duty.length} people with Y in "Working Today" are now on duty.`, "ok"); load();
    } catch (e) { toast(e.message, "error"); } finally { setBusy(false); }
  };

  const creds = result?.created || [];
  const credText = creds.map((c) => `${c.name} | ${c.email} | ${c.password}`).join("\n");

  return (
    <div className="page narrow">
      <div className="page-h"><div><h1>Today's roster</h1>
        <p className="muted">Add the people working today. Their accounts exist only for today and are cleared automatically at the day change. Waiting captures are shared out by priority, and each person is notified.</p></div></div>

      <div className="panel">
        <h2>From Excel</h2>
        <p className="muted">Put <code>Y</code> in the <em>Working Today</em> column of the Resources tab, then fetch it here. The Projects page lets you assign by hand.</p>
        <button className="btn" onClick={fetchFromExcel} disabled={busy}>Fetch today's people from Excel</button>
      </div>

      <div className="panel">
        <h2>Or add people by hand</h2>
        <label className="field">One person per line: name, email, password
          <textarea rows="5" placeholder={"Asha, asha@company.com, Asha@123\nRavi, ravi@company.com\nmeena@company.com"} value={lines} onChange={(e) => setLines(e.target.value)} />
        </label>
        <p className="muted small-note">Leave the password out and one is generated for you. Name is optional too.</p>

        {people.length > 0 && (
          <>
            <h2>Working today <span className="chip">{picked.size + (lines.trim() ? "+" : "")} selected</span></h2>
            <div className="pick-grid">
              {people.map((p) => (
                <label key={p.id} className={`pick ${picked.has(p.email) ? "on" : ""}`}>
                  <input type="checkbox" checked={picked.has(p.email)} onChange={() => flip(p.email)} />
                  <span className="avatar sm">{p.name[0]}</span>
                  <span><strong>{p.name}</strong><small className="muted">{p.email}</small></span>
                </label>
              ))}
            </div>
          </>
        )}

        <label className="check"><input type="checkbox" checked={distribute} onChange={(e) => setDistribute(e.target.checked)} />
          Read the sheet and assign waiting captures now</label>
        <button className="btn primary" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save roster"}</button>

        {result?.unknown?.length > 0 && <p className="error">No account for: {result.unknown.join(", ")}. Add them in the box above.</p>}
      </div>

      {creds.length > 0 && (
        <div className="panel creds">
          <h2>Share these logins</h2>
          <p className="muted">Passwords are shown only now. Copy them and send each person their own.</p>
          <div className="table-wrap"><table>
            <thead><tr><th>Name</th><th>Email</th><th>Password</th></tr></thead>
            <tbody>{creds.map((c) => <tr key={c.email}><td data-l="Name">{c.name}</td><td data-l="Email">{c.email}</td><td data-l="Password"><code>{c.password}</code></td></tr>)}</tbody>
          </table></div>
          <button className="btn" style={{ marginTop: 12 }} onClick={() => navigator.clipboard.writeText(credText).then(() => toast("Copied to clipboard", "ok"))}>Copy all</button>
        </div>
      )}
    </div>
  );
}
