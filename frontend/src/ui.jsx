import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "./api.js";

/* ---------- toasts ---------- */
const ToastCtx = createContext(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const push = useCallback((message, tone = "info") => {
    const id = Math.random().toString(36).slice(2);
    setItems((l) => [...l, { id, message, tone }]);
    setTimeout(() => setItems((l) => l.filter((t) => t.id !== id)), 5000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`}>{t.message}</div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/* ---------- formatting ---------- */
export const fmtTime = (iso) =>
  iso ? new Date(iso).toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
export const fmtMins = (m) => {
  if (m == null) return "—";
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
};
export const PRIORITY = { 1: "High", 2: "Medium", 3: "Low" };

/* ---------- stage track: assigned → picked → working → completed ---------- */
const STAGES = [
  ["assigned", "Assigned", "assigned_at"],
  ["picked", "Picked", "picked_at"],
  ["working", "Working", "started_at"],
  ["completed", "Completed", "completed_at"],
];
export function StageTrack({ capture }) {
  const idx = STAGES.findIndex(([k]) => k === capture.status);
  return (
    <ol className="track" aria-label={`Stage: ${capture.status_label}`}>
      {STAGES.map(([key, label, field], i) => (
        <li key={key} className={i < idx ? "past" : i === idx ? "now" : ""} data-stage={key}>
          <span className="dot" />
          <span className="lbl">{label}</span>
          <span className="when">{i <= idx ? fmtTime(capture[field]) : ""}</span>
        </li>
      ))}
    </ol>
  );
}

export function Badge({ status, children }) {
  return <span className={`badge ${status}`}>{children}</span>;
}

export function Modal({ title, onClose, children }) {
  useEffect(() => {
    const h = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="scrim" onMouseDown={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} onMouseDown={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        {children}
      </div>
    </div>
  );
}

/* ---------- notification bell (polls every 8s) ---------- */
export function Bell({ onNew }) {
  const [data, setData] = useState({ items: [], unread: 0 });
  const [open, setOpen] = useState(false);
  const seen = useRef(null);
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const d = await api("/notifications/");
      setData(d);
      const topId = d.items[0]?.id ?? 0;
      if (seen.current !== null && topId > seen.current) {
        d.items.filter((n) => n.id > seen.current && !n.is_read).reverse()
          .forEach((n) => toast(n.message, n.kind === "task" ? "task" : "info"));
        onNew?.();
      }
      seen.current = Math.max(seen.current ?? 0, topId);
    } catch { /* offline – try again next tick */ }
  }, [toast, onNew]);

  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);

  const toggle = async () => {
    setOpen((o) => !o);
    if (!open && data.unread) {
      await api("/notifications/", { method: "POST" });
      setTimeout(load, 300);
    }
  };

  return (
    <div className="bell">
      <button className="icon-btn" onClick={toggle} aria-label={`Notifications, ${data.unread} unread`}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8" /><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
        </svg>
        {data.unread > 0 && <span className="pip">{data.unread > 9 ? "9+" : data.unread}</span>}
      </button>
      {open && (
        <div className="drop" role="menu">
          <div className="drop-h">Notifications</div>
          {data.items.length === 0 && <p className="muted pad">Nothing yet.</p>}
          {data.items.map((n) => (
            <div key={n.id} className={`note ${n.kind}`}>
              <p>{n.message}</p>
              <time>{fmtTime(n.created_at)}</time>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
