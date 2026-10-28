import { useState } from "react";
import { Navigate, NavLink, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { clearSession, getToken, savedUser } from "./api.js";
import { Bell, ToastProvider } from "./ui.jsx";
import Login from "./pages/Login.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Roster from "./pages/Roster.jsx";
import Projects from "./pages/Projects.jsx";
import Resources from "./pages/Resources.jsx";
import Home from "./pages/Home.jsx";

function Shell({ role }) {
  const user = savedUser();
  const nav = useNavigate();
  const [bump, setBump] = useState(0);
  if (!getToken() || !user) return <Navigate to="/login" replace />;
  if (user.role !== role) return <Navigate to={user.role === "admin" ? "/admin" : "/"} replace />;

  const links = role === "admin"
    ? [["/admin", "Dashboard", true], ["/admin/roster", "Today's roster"], ["/admin/projects", "Projects"], ["/admin/resources", "Resources"]]
    : [["/", "My work", true]];

  return (
    <div className="app">
      <aside className="side">
        <div className="brand"><span className="mark" />CaptureFlow</div>
        <nav>
          {links.map(([to, label, end]) => (
            <NavLink key={to} to={to} end={end}>{label}</NavLink>
          ))}
        </nav>
        <div className="me">
          <div className="avatar">{user.name?.[0]?.toUpperCase()}</div>
          <div className="who"><strong>{user.name}</strong><span>{user.email}</span></div>
          <button className="link" onClick={() => { clearSession(); nav("/login"); }}>Sign out</button>
        </div>
      </aside>
      <main className="main">
        <header className="top">
          <span className="role">{role === "admin" ? "Admin console" : "Resource workspace"}</span>
          <Bell onNew={() => setBump((b) => b + 1)} />
        </header>
        <Outlet context={{ bump }} />
      </main>
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<Shell role="admin" />}>
          <Route path="/admin" element={<Dashboard />} />
          <Route path="/admin/roster" element={<Roster />} />
          <Route path="/admin/projects" element={<Projects />} />
          <Route path="/admin/resources" element={<Resources />} />
        </Route>
        <Route element={<Shell role="resource" />}>
          <Route path="/" element={<Home />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </ToastProvider>
  );
}
