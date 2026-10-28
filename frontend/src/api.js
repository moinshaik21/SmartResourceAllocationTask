const BASE = (import.meta.env.VITE_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export const getToken = () => localStorage.getItem("cf_token");
export const setSession = (token, user) => {
  localStorage.setItem("cf_token", token);
  localStorage.setItem("cf_user", JSON.stringify(user));
};
export const clearSession = () => {
  localStorage.removeItem("cf_token");
  localStorage.removeItem("cf_user");
};
export const savedUser = () => {
  try { return JSON.parse(localStorage.getItem("cf_user")); } catch { return null; }
};

export async function api(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(`${BASE}/api${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        ...(getToken() ? { Authorization: `Token ${getToken()}` } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("Can't reach the server. Check your connection and try again.");
  }
  if (res.status === 401 && getToken()) {
    clearSession();
    window.location.assign("/login");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (data.code === "ms_expired") window.dispatchEvent(new Event("ms-expired"));   // dashboard shows "sign in again"
    const e = new Error(data.detail || "Something went wrong.");
    e.code = data.code;
    throw e;
  }
  return data;
}
