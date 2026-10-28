import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api, getToken, savedUser, setSession } from "../api.js";

export default function Login() {
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const u = savedUser();
  if (getToken() && u) return <Navigate to={u.role === "admin" ? "/admin" : "/"} replace />;

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      const { token, user } = await api("/auth/login/", { method: "POST", body: { email, password } });
      setSession(token, user);
      nav(user.role === "admin" ? "/admin" : "/");
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  };

  return (
    <div className="login">
      <section className="login-art" aria-hidden="true">
        <div className="brand big"><span className="mark" />CaptureFlow</div>
        <p className="tagline">Every capture, every split, one clear owner.</p>
        <div className="demo-track">
          {["Assigned", "Picked", "Working", "Completed"].map((s, i) => (
            <div key={s} className={`demo-step s${i}`}><span />{s}</div>
          ))}
        </div>
      </section>
      <form className="login-card" onSubmit={submit}>
        <h1>Sign in</h1>
        <p className="muted">Use the email and password your admin gave you.</p>
        <label>Email<input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label>Password<input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="btn primary wide" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </div>
  );
}
