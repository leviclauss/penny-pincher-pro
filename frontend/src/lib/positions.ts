import type { PositionState } from "@/api/types";

export const STATE_LABELS: Record<string, string> = {
  short_put: "Short put",
  long_shares: "Long shares",
  covered_call: "Covered call",
  closed: "Closed",
};

export const STATE_TONES: Record<string, string> = {
  short_put: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
  long_shares: "bg-violet-500/15 text-violet-300 ring-violet-500/30",
  covered_call: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  closed: "bg-muted text-muted-foreground ring-border",
};

export const ACTIVE_STATES: PositionState[] = ["short_put", "long_shares", "covered_call"];

export function isOpen(state: string): boolean {
  return state !== "closed";
}

export function todayIso(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function formatCurrency(
  value: number | null | undefined,
  digits = 2,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  return `${sign}$${abs.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

export function pnlTone(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "text-muted-foreground";
  }
  if (value > 0) return "text-emerald-300";
  if (value < 0) return "text-red-300";
  return "text-foreground";
}
