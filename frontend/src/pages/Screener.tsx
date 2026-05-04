import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { ArrowDown, ArrowUp, ArrowUpDown, Pencil, Settings2 } from "lucide-react";
import { fetchScreenerConfigs, fetchScreenerResults } from "@/api/client";
import type { ScreenerResultRow } from "@/api/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Checkbox } from "@/components/ui/Checkbox";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/Table";
import { cn } from "@/lib/utils";
import { formatDate, formatNumber, formatPercent } from "@/lib/format";

type SortKey =
  | "score"
  | "symbol"
  | "sector"
  | "rsi_14"
  | "iv_percentile"
  | "iv_rank"
  | "near_200ema_pct"
  | "next_earnings_date";
type SortDir = "asc" | "desc";

interface Column {
  key: SortKey;
  label: string;
  align?: "left" | "right";
}

const COLUMNS: Column[] = [
  { key: "score", label: "Score", align: "right" },
  { key: "symbol", label: "Symbol" },
  { key: "sector", label: "Sector" },
  { key: "near_200ema_pct", label: "Δ 200 EMA", align: "right" },
  { key: "rsi_14", label: "RSI", align: "right" },
  { key: "iv_percentile", label: "IV %ile", align: "right" },
  { key: "iv_rank", label: "IV Rank", align: "right" },
  { key: "next_earnings_date", label: "Next ER", align: "right" },
];

function getSortValue(r: ScreenerResultRow, key: SortKey): string | number | null {
  switch (key) {
    case "score":
      return r.score;
    case "symbol":
      return r.symbol;
    case "sector":
      return r.sector;
    case "rsi_14":
      return r.rsi_14;
    case "iv_percentile":
      return r.iv_percentile;
    case "iv_rank":
      return r.iv_rank;
    case "near_200ema_pct":
      return r.near_200ema_pct;
    case "next_earnings_date":
      return r.next_earnings_date;
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

function scoreTone(value: number | null): string {
  if (value === null) return "text-muted-foreground";
  if (value >= 75) return "text-emerald-300";
  if (value >= 50) return "text-sky-300";
  return "text-foreground";
}

export function Screener(): JSX.Element {
  const navigate = useNavigate();
  const [configId, setConfigId] = useState<number | null>(null);
  const [passedOnly, setPassedOnly] = useState(true);
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const configsQuery = useQuery({
    queryKey: ["screener", "configs"],
    queryFn: () => fetchScreenerConfigs(false),
  });

  // Default to the first active config once configs land.
  useEffect(() => {
    if (configId !== null) return;
    const first = configsQuery.data?.find((c) => c.is_active) ?? configsQuery.data?.[0];
    if (first) setConfigId(first.id);
  }, [configId, configsQuery.data]);

  const resultsQuery = useQuery({
    queryKey: ["screener", "results", { configId, passedOnly }],
    queryFn: () => fetchScreenerResults({ configId, passedOnly }),
    enabled: configId !== null,
  });

  const sorted = useMemo(() => {
    if (!resultsQuery.data) return [];
    const out = [...resultsQuery.data.rows];
    out.sort((a, b) => {
      const cmp = compareValues(getSortValue(a, sortKey), getSortValue(b, sortKey));
      return sortDir === "asc" ? cmp : -cmp;
    });
    return out;
  }, [resultsQuery.data, sortKey, sortDir]);

  const toggleSort = (key: SortKey): void => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "symbol" || key === "sector" ? "asc" : "desc");
    }
  };

  return (
    <div className="space-y-6">
      <header className="space-y-1.5">
        <p className="text-primary text-xs font-semibold uppercase tracking-widest">
          Strategy
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">Screener</h1>
        <p className="text-muted-foreground max-w-2xl text-sm">
          Wheel candidates ranked by your filter configs. Each row is the latest
          decision for that symbol; click through for the ticker detail page.
        </p>
      </header>

      <Card>
        <CardHeader className="px-3 sm:px-5">
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <CardTitle>Candidates</CardTitle>
              <div className="flex items-center gap-1">
                <select
                  value={configId ?? ""}
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v === "__new__") {
                      navigate("/screener/configs/new");
                      return;
                    }
                    setConfigId(v ? Number(v) : null);
                  }}
                  disabled={configsQuery.isLoading}
                  className="border-border bg-background text-foreground focus-visible:ring-ring h-8 max-w-[180px] rounded-md border px-2 text-xs focus-visible:outline-none focus-visible:ring-2"
                >
                  {configsQuery.data?.length === 0 && (
                    <option value="">No configs</option>
                  )}
                  {configsQuery.data?.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                      {c.is_active ? "" : " (inactive)"}
                    </option>
                  ))}
                  <option value="__new__">+ New config…</option>
                </select>
                {configId !== null && (
                  <Link
                    to={`/screener/configs/${configId}`}
                    title="Edit config"
                    className="text-muted-foreground hover:bg-accent hover:text-foreground inline-flex h-8 w-8 items-center justify-center rounded-md transition-colors"
                  >
                    <Pencil className="h-4 w-4" />
                  </Link>
                )}
                <Link
                  to="/screener/configs"
                  title="Manage configs"
                  className="text-muted-foreground hover:bg-accent hover:text-foreground inline-flex h-8 w-8 items-center justify-center rounded-md transition-colors"
                >
                  <Settings2 className="h-4 w-4" />
                </Link>
              </div>
              <Checkbox
                label="Passed only"
                checked={passedOnly}
                onChange={(e) => setPassedOnly(e.target.checked)}
              />
            </div>
            <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 text-xs">
              {resultsQuery.data && (
                <>
                  <span>as of {formatDate(resultsQuery.data.date)}</span>
                  <span>{resultsQuery.data.rows.length} rows</span>
                </>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="px-3 pb-3 sm:px-5 sm:pb-5">
          {configsQuery.isLoading && (
            <div className="text-muted-foreground text-sm">Loading configs…</div>
          )}
          {configsQuery.isError && (
            <div className="text-destructive text-sm">Failed to load configs.</div>
          )}
          {configsQuery.data && configsQuery.data.length === 0 && (
            <div className="text-muted-foreground text-sm">
              No filter configs yet. Run{" "}
              <code className="font-mono">python -m scripts.seed_filter_configs</code> to
              add the default.
            </div>
          )}
          {resultsQuery.isLoading && configId !== null && (
            <div className="text-muted-foreground text-sm">Loading results…</div>
          )}
          {resultsQuery.data && resultsQuery.data.rows.length === 0 && (
            <div className="text-muted-foreground text-sm">
              No results yet for this config. Trigger the{" "}
              <code className="font-mono">screener_pipeline</code> job from the Jobs
              page or wait for the evening run.
            </div>
          )}
          {sorted.length > 0 && (
            <>
              <div className="md:hidden">
                <ScreenerMobileSortControl
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSortKeyChange={(k) => {
                    setSortKey(k);
                    setSortDir(k === "symbol" || k === "sector" ? "asc" : "desc");
                  }}
                  onToggleDir={() => setSortDir((d) => (d === "asc" ? "desc" : "asc"))}
                />
                <ul className="divide-border/50 -mx-1 mt-2 divide-y">
                  {sorted.map((r) => (
                    <ScreenerMobileRow
                      key={`${r.symbol}-${r.config_id}`}
                      row={r}
                      onOpen={() => navigate(`/tickers/${r.symbol}`)}
                    />
                  ))}
                </ul>
              </div>

              <div className="hidden md:block">
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
                      <TableHead>Result</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {sorted.map((r) => {
                      const distancePct =
                        r.near_200ema_pct === null ? null : r.near_200ema_pct * 100;
                      return (
                        <TableRow
                          key={`${r.symbol}-${r.config_id}`}
                          onClick={() => navigate(`/tickers/${r.symbol}`)}
                          className="cursor-pointer"
                        >
                          <TableCell
                            className={cn(
                              "text-right font-mono font-semibold",
                              scoreTone(r.score),
                            )}
                          >
                            {r.score === null ? "—" : r.score.toFixed(0)}
                          </TableCell>
                          <TableCell className="font-semibold tracking-tight">
                            {r.symbol}
                          </TableCell>
                          <TableCell className="text-muted-foreground">
                            {r.sector ?? "—"}
                          </TableCell>
                          <TableCell className="text-right font-mono">
                            {formatPercent(distancePct)}
                          </TableCell>
                          <TableCell className={cn("text-right font-mono", rsiTone(r.rsi_14))}>
                            {formatNumber(r.rsi_14, 1)}
                          </TableCell>
                          <TableCell className="text-right font-mono">
                            {formatNumber(r.iv_percentile, 0)}
                          </TableCell>
                          <TableCell className="text-right font-mono">
                            {formatNumber(r.iv_rank, 0)}
                          </TableCell>
                          <TableCell className="text-muted-foreground text-right font-mono text-xs">
                            {formatDate(r.next_earnings_date)}
                          </TableCell>
                          <TableCell>
                            <span
                              className={cn(
                                "inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ring-1",
                                r.passed
                                  ? "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30"
                                  : "bg-muted text-muted-foreground ring-border",
                              )}
                            >
                              {r.passed ? "Pass" : "Fail"}
                            </span>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

interface ScreenerMobileSortControlProps {
  sortKey: SortKey;
  sortDir: SortDir;
  onSortKeyChange: (key: SortKey) => void;
  onToggleDir: () => void;
}

function ScreenerMobileSortControl({
  sortKey,
  sortDir,
  onSortKeyChange,
  onToggleDir,
}: ScreenerMobileSortControlProps): JSX.Element {
  return (
    <div className="flex items-center gap-2">
      <span className="text-muted-foreground text-[11px] font-medium uppercase tracking-wider">
        Sort
      </span>
      <select
        value={sortKey}
        onChange={(e) => onSortKeyChange(e.target.value as SortKey)}
        className="border-border bg-background text-foreground focus-visible:ring-ring h-8 flex-1 rounded-md border px-2 text-sm focus-visible:outline-none focus-visible:ring-2"
        aria-label="Sort by"
      >
        {COLUMNS.map((c) => (
          <option key={c.key} value={c.key}>
            {c.label}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={onToggleDir}
        className="border-border bg-background hover:bg-accent inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border"
        title={sortDir === "asc" ? "Ascending" : "Descending"}
        aria-label={`Toggle sort direction (currently ${sortDir})`}
      >
        {sortDir === "asc" ? (
          <ArrowUp className="h-4 w-4" />
        ) : (
          <ArrowDown className="h-4 w-4" />
        )}
      </button>
    </div>
  );
}

function ScreenerMobileRow({
  row,
  onOpen,
}: {
  row: ScreenerResultRow;
  onOpen: () => void;
}): JSX.Element {
  const distancePct = row.near_200ema_pct === null ? null : row.near_200ema_pct * 100;
  return (
    <li
      onClick={onOpen}
      className="active:bg-accent/40 cursor-pointer px-1 py-3 transition-colors"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-base font-semibold tracking-tight">{row.symbol}</span>
            <span
              className={cn(
                "inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ring-1",
                row.passed
                  ? "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30"
                  : "bg-muted text-muted-foreground ring-border",
              )}
            >
              {row.passed ? "Pass" : "Fail"}
            </span>
          </div>
          <div className="text-muted-foreground mt-0.5 truncate text-xs">
            {row.sector ?? "—"}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className={cn("font-mono text-base", scoreTone(row.score))}>
            {row.score === null ? "—" : row.score.toFixed(0)}
          </div>
          <div className="text-muted-foreground text-[10px] uppercase tracking-wider">
            score
          </div>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2">
        <div className="bg-muted/30 rounded-md px-2 py-1.5">
          <div className="text-muted-foreground text-[10px] font-medium uppercase tracking-wider">
            Δ 200 EMA
          </div>
          <div className="font-mono text-sm">{formatPercent(distancePct)}</div>
        </div>
        <div className="bg-muted/30 rounded-md px-2 py-1.5">
          <div className="text-muted-foreground text-[10px] font-medium uppercase tracking-wider">
            RSI
          </div>
          <div className={cn("font-mono text-sm", rsiTone(row.rsi_14))}>
            {formatNumber(row.rsi_14, 1)}
          </div>
        </div>
        <div className="bg-muted/30 rounded-md px-2 py-1.5">
          <div className="text-muted-foreground text-[10px] font-medium uppercase tracking-wider">
            IV %ile
          </div>
          <div className="font-mono text-sm">{formatNumber(row.iv_percentile, 0)}</div>
        </div>
      </div>
    </li>
  );
}
