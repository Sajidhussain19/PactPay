export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8010").replace(/\/$/, "");

const TOKEN_KEY = "pactpay.token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  const response = await fetch(`${API_URL}${path}`, { ...init, headers, body });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      // keep the status text
    }
    throw new ApiError(response.status, message);
  }
  return (await response.json()) as T;
}

/** Evidence screenshots are served by the backend under /artifacts. */
export function assetUrl(path: string): string {
  return path.startsWith("http") ? path : `${API_URL}${path}`;
}

export function streamUrl(milestoneId: string): string {
  // EventSource can't send headers, so the token goes in the query string.
  return `${API_URL}/milestones/${milestoneId}/verification/stream?token=${encodeURIComponent(getToken() ?? "")}`;
}

export const DEMO_SITES = [
  { id: "buggy-site", label: "Buggy site", hint: "broken form, not mobile friendly" },
  { id: "good-site", label: "Fixed site", hint: "everything works" },
  { id: "malicious-site", label: "Malicious site", hint: "tries to trick the AI" },
  { id: "sneaky-site", label: "Sneaky site", hint: "works, but hides instructions to the AI" },
] as const;

export function demoSiteUrl(id: string): string {
  return `${API_URL}/demo-sites/${id}/`;
}
