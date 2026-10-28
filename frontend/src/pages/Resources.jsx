import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { useToast } from "../ui.jsx";

export default function Resources() {
  const toast = useToast();
  const [list, setList] = useState([]);
  const [form, setForm] = useState({ name: "", email: "", password: "", senior_for: "" });
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api("/resources/").then(setList).catch((e) => toast(e.message, "error")), [toast]);
  useEffect(() => { load(); }, [load]);

  const create = async (e) => {
    e.preventDefault(); setBusy(true);
    try {
      await api("/resources/", { method: "POST", body: form });
      toast(`${form.name} can now sign in with ${form.email}`, "ok");
      setForm({ name: "", email: "", password: "", senior_for: "" }); load();
    } catch (err) { toast(err.message, "error"); } finally { setBusy(false); }
  };

  const patch = async (id, body, msg) => {
    try { await api(`/resources/${id}/`, { method: "PATCH", body }); toast(msg, "ok"); load(); }
    catch (err) { toast(err.message, "error"); }
  };

  const resetPw = (u) => {
    const pw = window.prompt(`New password for ${u.name} (min 6 characters)`);
    if (pw) patch(u.id, { password: pw }, "Password updated");
  };

  return (
    <div className="page">
      <div className="page-h"><div><h1>Resources</h1><p className="muted">People who sign in with their own email and password to see their assigned work.</p></div></div>
      <form className="panel form-row" onSubmit={create}>
        <label className="field">Name<input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        <label className="field">Email<input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
        <label className="field">Password<input type="text" minLength="6" required value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
        <label className="field">Senior for (optional)<input value={form.senior_for} placeholder="project names, comma separated" onChange={(e) => setForm({ ...form, senior_for: e.target.value })} /></label>
        <button className="btn primary" disabled={busy}>{busy ? "Adding…" : "Add resource"}</button>
      </form>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Name</th><th>Email</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {list.map((u) => (
              <tr key={u.id}>
                <td data-l="Name"><strong>{u.name}</strong>{u.senior_for && <small className="muted"> · Senior: {u.senior_for}</small>}</td>
                <td data-l="Email">{u.email}</td>
                <td data-l="Status"><span className={`duty ${u.is_active ? "on" : ""}`}>{u.is_active ? "Active" : "Disabled"}</span></td>
                <td className="row-actions">
                  <button className="btn sm" onClick={() => resetPw(u)}>Reset password</button>
                  <button className="btn sm" onClick={() => patch(u.id, { is_active: !u.is_active }, u.is_active ? "Disabled" : "Enabled")}>{u.is_active ? "Disable" : "Enable"}</button>
                </td>
              </tr>
            ))}
            {list.length === 0 && <tr><td colSpan="4" className="muted center">No resources yet. Add the first one above.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
