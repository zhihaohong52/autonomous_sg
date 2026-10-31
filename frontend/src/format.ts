import type { AgentId, Sender } from "./api/types";

/** Airport-local clock time for a minute offset from the window start (keeps the ISO string's own offset). */
export function clock(windowStart: string, min: number): string {
  const m = windowStart.match(/T(\d{2}):(\d{2})/);
  if (!m) return `T${min >= 0 ? "+" : ""}${min}`;
  const total = (+m[1] * 60 + +m[2] + Math.round(min) + 1440) % 1440;
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

export function dateLabel(iso: string): string {
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return iso;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${+m[3]} ${months[+m[2] - 1]} ${m[1]}`;
}

export const signed = (n: number, unit = "") => (n === 0 ? `0${unit}` : `${n > 0 ? "+" : "−"}${Math.abs(n)}${unit}`);
export const fmtInt = (n: number) => n.toLocaleString("en-US");

export function agentClass(s: Sender): string {
  if (s.startsWith("PILOT")) return "pilot";
  return s.toLowerCase();
}
export const agentName = (s: Sender | AgentId) => (s.startsWith("PILOT:") ? `Pilot ${s.slice(6)}` : s === "ATC" ? "ATC" : s === "GROUND" ? "Ground" : s === "ENGINE" ? "Engine" : "Triage");
