import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { fetchTickers } from "@/api/client";
import type { TickerSummary } from "@/api/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/Table";
import { cn } from "@/lib/utils";
import { formatDate, formatNumber, formatPercent, pctDistance } from "@/lib/format";

type SortKey =
  | "symbol"
  | "name"
  | "tier"
  | "sector"
  | "last_close"
  | "distance_200"
  | "rsi_14"
  | "iv_atm"
  | "next_earnings_date";
type SortDir = "asc" | "desc";

interface Column {
  key: SortKey;
  label: string;
  align?: "left" | "right";
}

const COLUMNS: Column[] = [
  { key: "symbol", label: "Symbol" },
  { key: "name", label: "Name" },
  { key: "tier", label: "Tier", align: "right" },
  { key: "sector", label: "Sector" },
  { key: "last_close", label: "Last", align: "right" },
  { key: "distance_200", label: "Δ 200 EMA", align: "right" },
  { key: "rsi_14", label: "RSI", align: "right" },
  { key: "iv_atm", label: "IV ATM", align: "right" },
  { key: "next_earnings_date", label: "Next ER", align: "right" },
];

function getSortValue(t: TickerSummary, key: SortKey): string | number | null {
  switch (key) {
    case "symbol":
      return t.symbol;
    case "name":
      return t.name;
    case "tier":
      return t.tier;
    case "sector":
      return t.sector;
    case "last_close":
      return t.last_close;
    case "distance_200":
      return pctDistance(t.last_close, t.ema_200);
    case "rsi_14":
      return t.rsi_14;
    case "iv_atm":
      return t.iv_atm;
    case "next_earnings_date":
      return t.next_earnings_date;
  }
}

function compareValues(a: string | number | null, b: string | number | null): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b));
}

function rsiTone(value: number | null): string {
  if (value === null) return "text-muted-foreground";
  if (value >= 70) return "text-amber-300";
  if (value <= 30) return "text-emerald-300";
  return "text-foreground";
}

function tierBadge(tier: number | null): JSX.Element {
  if (tier === null) return <span className="text-muted-foreground">—</span>;
  const palette: Record<number, string> = {
    1: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
    2: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
    3: "bg-violet-500/15 text-violet-300 ring-violet-500/30",
  };
  const cls = palette[tier] ?? "bg-muted text-muted-foreground ring-border";
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ring-1",
        cls,
      )}
    >
      T{tier}
    </span>
  );
}

export function Tickers(): JSX.Element {
  const [sortKey, setSortKey] = useState<SortKey>("symbol");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const navigate = useNavigate();

  const { data, isLoading, isError } = useQuery({
    queryKey: ["tickers"],
    queryFn: fetchTickers,
  });

  const sorted = useMemo(() => {
    if (!data) return [];
    const out = [...data];
    out.sort((a, b) => {
      const cmp = compareValues(getSortValue(a, sortKey), getSortValue(b, sortKey));
      return sortDir === "asc" ? cmp : -cmp;
    });
    return out;
  }, [data, sortKey, sortDir]);

  const toggleSort = (key: SortKey): void => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  return (
    <div className="space-y-6">
      <header className="space-y-1.5">
        <p className="text-primary text-xs font-semibold uppercase tracking-widest">
          Watchlist
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">Tickers</h1>
        <p className="text-muted-foreground max-w-2xl text-sm">
          Latest close, EMA distance, RSI, IV, and the next earnings event for
          every symbol on your list.
        </p>
      </header>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>All symbols</CardTitle>
            <span className="text-muted-foreground text-xs">
              {data ? `${data.length} symbols` : "—"}
            </span>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading && <div className="text-muted-foreground text-sm">Loading…</div>}
          {isError && (
            <div className="text-destructive text-sm">Failed to load tickers.</div>
          )}
          {data && data.length === 0 && (
            <div className="text-muted-foreground text-sm">
              Watchlist is empty. Run{" "}
              <code className="bg-muted rounded px-1 py-0.5 text-xs">
                python -m scripts.seed_dev
              </code>{" "}
              in the backend to populate it.
            </div>
          )}
          {data && data.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  {COLUMNS.map((col) => {
                    const active = sortKey === col.key;
                    return (
                      <TableHead
                        key={col.key}
                        className={cn(
                          "hover:text-foreground cursor-pointer select-none transition-colors",
                          col.align === "right" && "text-right",
                          active && "text-foreground",
                        )}
                        onClick={() => toggleSort(col.key)}
                      >
                        <span
                          className={cn(
                            "inline-flex items-center gap-1.5",
                            col.align === "right" && "justify-end",
                          )}
                        >
                          {col.label}
                          {active ? (
                            sortDir === "asc" ? (
                              <ArrowUp className="text-primary h-3 w-3" />
                            ) : (
                              <ArrowDown className="text-primary h-3 w-3" />
                            )
                          ) : (
                            <ArrowUpDown className="h-3 w-3 opacity-40" />
                          )}
                        </span>
                      </TableHead>
                    );
                  })}
                </TableRow>
              </TableHeader>
              <TableBody>
                {sorted.map((t) => {
                  const distance = pctDistance(t.last_close, t.ema_200);
                  return (
                    <TableRow
                      key={t.symbol}
                      onClick={() => navigate(`/tickers/${t.symbol}`)}
                      className="cursor-pointer"
                    >
                      <TableCell className="font-semibold tracking-tight">
                        {t.symbol}
                      </TableCell>
                      <TableCell className="text-muted-foreground max-w-[180px] truncate">
                        {t.name ?? "—"}
                      </TableCell>
                      <TableCell className="text-right">{tierBadge(t.tier)}</TableCell>
                      <TableCell className="text-muted-foreground">
                        {t.sector ?? "—"}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {formatNumber(t.last_close)}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right font-mono",
                          distance !== null && distance >= 0 && "text-emerald-300",
                          distance !== null && distance < 0 && "text-red-300",
                        )}
                      >
                        {formatPercent(distance)}
                      </TableCell>
                      <TableCell
                        className={cn("text-right font-mono", rsiTone(t.rsi_14))}
                      >
                        {formatNumber(t.rsi_14, 1)}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {t.iv_atm === null ? "—" : `${(t.iv_atm * 100).toFixed(1)}%`}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-right font-mono text-xs">
                        {formatDate(t.next_earnings_date)}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
