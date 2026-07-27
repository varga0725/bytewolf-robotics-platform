export type ApiError = Error & { status?: number };

function sessionId(): string {
  // sessionStorage, not localStorage: this identity is what binds a mission
  // approval to the operator who gave it. localStorage survives browser
  // restarts, so the next person at the same profile would have inherited the
  // previous operator's session -- and with it their approvals.
  const stored = window.sessionStorage.getItem("bytewolfSession");
  if (stored) return stored;

  const value = window.crypto.randomUUID();
  window.sessionStorage.setItem("bytewolfSession", value);
  return value;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-ByteWolf-Session", sessionId());
  if (init.body !== undefined) headers.set("Content-Type", "application/json");

  const response = await fetch(path, { ...init, headers });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      typeof payload === "object" && payload !== null && "detail" in payload
        ? String(payload.detail)
        : "A kérés sikertelen.";
    const error: ApiError = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return payload as T;
}

export function post<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, { method: "POST", body: JSON.stringify(body) });
}
