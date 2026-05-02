import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowDown, ArrowUp } from "lucide-react";
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
import { formatNumber, formatPercent, pctDistance } from "@/lib/format";

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
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold">Tickers</h1>
        <p className="text-muted-foreground text-sm">
          Watchlist with the latest close, EMA distance, RSI, IV, and next earnings.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Watchlist</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading && <div className="text-muted-foreground text-sm">Loading…</div>}
          {isError && (
            <div className="text-destructive text-sm">Failed to load tickers.</div>
          )}
          {data && data.length === 0 && (
            <div className="text-muted-foreground text-sm">
              Watchlist is empty. Run <code>python -m scripts.seed_dev</code> in the
              backend to populate it.
            </div>
          )}
          {data && data.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  {COLUMNS.map((col) => (
                    <TableHead
                      key={col.key}
                      className={cn(
                        "cursor-pointer select-none",
                        col.align === "right" && "text-right",
                      )}
                      onClick={() => toggleSort(col.key)}
                    >
                      <span
                        className={cn(
                          "inline-flex items-center gap-1",
                          col.align === "right" && "justify-end",
                        )}
                      >
                        {col.label}
                        {sortKey === col.key &&
                          (sortDir === "asc" ? (
                            <ArrowUp className="h-3 w-3" />
                          ) : (
                            <ArrowDown className="h-3 w-3" />
                          ))}
                      </span>
                    </TableHead>
                  ))}
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
                      <TableCell className="font-semibold">{t.symbol}</TableCell>
                      <TableCell className="text-muted-foreground">{t.name ?? "—"}</TableCell>
                      <TableCell className="text-right">{t.tier ?? "—"}</TableCell>
                      <TableCell className="text-muted-foreground">
                        {t.sector ?? "—"}
                      </TableCell>
                      <TableCell className="text-right">{formatNumber(t.last_close)}</TableCell>
                      <TableCell
                        className={cn(
                          "text-right",
                          distance !== null && distance >= 0 && "text-emerald-600",
                          distance !== null && distance < 0 && "text-red-600",
                        )}
                      >
                        {formatPercent(distance)}
                      </TableCell>
                      <TableCell className="text-right">{formatNumber(t.rsi_14, 1)}</TableCell>
                      <TableCell className="text-right">
                        {t.iv_atm === null ? "—" : formatNumber(t.iv_atm * 100, 1) + "%"}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {t.next_earnings_date ?? "—"}
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
