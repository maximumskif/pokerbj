// Thin client for the local backend. The API key never reaches the browser.
/* eslint-disable @typescript-eslint/no-explicit-any */

export type Json = any;

async function req(method: string, path: string, body?: unknown): Promise<Json> {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error(data?.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText);
  return data;
}

export const api = {
  get: (p: string) => req("GET", p),
  post: (p: string, b?: unknown) => req("POST", p, b ?? {}),
  put: (p: string, b: unknown) => req("PUT", p, b),
  del: (p: string) => req("DELETE", p),
};

export function connectState(onState: (s: Json) => void, onConn: (ok: boolean) => void): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let retry: number | undefined;
  const open = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen = () => onConn(true);
    ws.onmessage = (e) => onState(JSON.parse(e.data));
    ws.onclose = () => {
      onConn(false);
      if (!closed) retry = window.setTimeout(open, 1500);
    };
  };
  open();
  return () => {
    closed = true;
    if (retry) clearTimeout(retry);
    ws?.close();
  };
}

export const SUIT: Record<string, string> = { c: "♣", d: "♦", h: "♥", s: "♠" };
export const isRed = (c: string) => c.endsWith("d") || c.endsWith("h");
export const rankLabel = (r: string) => (r === "T" ? "10" : r);

export function pct(x: number | null | undefined, d = 1): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return `${(x * 100).toFixed(d)}%`;
}

export function signed(x: number | null | undefined, d = 3): string {
  if (x === null || x === undefined) return "—";
  return `${x >= 0 ? "+" : ""}${x.toFixed(d)}`;
}

export function ago(sec: number | null | undefined): string {
  if (sec === null || sec === undefined) return "—";
  if (sec < 1) return "<1s";
  if (sec < 60) return `${Math.floor(sec)}s`;
  return `${Math.floor(sec / 60)}m ${Math.floor(sec % 60)}s`;
}
