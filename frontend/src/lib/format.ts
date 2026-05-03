export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatPercent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}%`;
}

const PACIFIC_TZ = "America/Los_Angeles";
const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/;
const HAS_TZ_RE = /(Z|[+-]\d{2}:?\d{2})$/;

function parseDateOnly(value: string): Date {
  const [y, m, d] = value.split("-").map(Number);
  return new Date(y, m - 1, d);
}

// Backend stores datetimes as UTC, but SQLite returns them naive, so the
// JSON serialization lacks a timezone suffix. Without this, JS would parse
// them as local time and our formatters would mislabel the clock value.
function parseAsUtc(value: string): Date {
  return new Date(HAS_TZ_RE.test(value) ? value : `${value}Z`);
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  if (DATE_ONLY_RE.test(value)) {
    return parseDateOnly(value).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  }
  const parsed = parseAsUtc(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: PACIFIC_TZ,
  });
}

export function formatDateShort(value: string | null | undefined): string {
  if (!value) return "—";
  if (DATE_ONLY_RE.test(value)) {
    return parseDateOnly(value).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
    });
  }
  const parsed = parseAsUtc(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: PACIFIC_TZ,
  });
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = parseAsUtc(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: PACIFIC_TZ,
    timeZoneName: "short",
  });
}

export function pctDistance(from: number | null | undefined, to: number | null | undefined): number | null {
  if (from === null || from === undefined || to === null || to === undefined || to === 0) {
    return null;
  }
  return ((from - to) / to) * 100;
}
