import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  ChevronDown,
  ChevronUp,
  Download,
  FlaskConical,
  GitCompareArrows,
  Loader2,
  Play,
  Trash2,
} from "lucide-react";
import {
  ApiError,
  backtestTradesCsvUrl,
  deleteBacktestRun,
  fetchBacktestCoverage,
  fetchBacktestEquity,
  fetchBacktestRun,
  fetchBacktestRuns,
  fetchBacktestTrades,
  fetchScreenerConfigs,
  runBacktest,
} from "@/api/client";
import type {
  BacktestCoverageOut,
  BacktestMode,
  BacktestRunIn,
  BacktestRunOut,
  BacktestStatus,
  StrategyParamsIn,
} from "@/api/types";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonVariants } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Checkbox } from "@/components/ui/Checkbox";
import { Input } from "@/components/ui/Input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/Table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";
import { DrawdownChart } from "@/components/charts/DrawdownChart";
import { EquityChart } from "@/components/charts/EquityChart";
import { ReturnHistogram } from "@/components/charts/ReturnHistogram";
import { formatDate, formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { BacktestTradeOut } from "@/api/types";

function fmt(value: number | null | undefined, digits = 2): string {
  if (value == null) return "—";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtMoney(value: number | null | undefined): string {
  if (value == null) return "—";
  return `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function ReturnText({ value }: { value: number | null }): JSX.Element {
  if (value == null) return <span className="text-muted-foreground">—</span>;
  const positive = value >= 0;
  return (
    <span
      className={cn(
        "font-mono font-medium",
        positive ? "text-emerald-500" : "text-red-500",
      )}
    >
      {positive ? "+" : ""}
      {fmt(value)}%
    </span>
  );
}

function StatusBadge({ status }: { status: BacktestStatus }): JSX.Element {
  if (status === "completed") {
    return <Badge variant="success">completed</Badge>;
  }
  if (status === "failed") {
    return <Badge variant="destructive">failed</Badge>;
  }
  return (
    <Badge variant="outline" className="gap-1">
      <Loader2 className="h-3 w-3 animate-spin" />
      running
    </Badge>
  );
}

function ModeBadge({ mode }: { mode: BacktestMode }): JSX.Element {
  return (
    <Badge variant={mode === "strategy" ? "default" : "outline"}>{mode}</Badge>
  );
}

function StrategyEquityPanel({
  runId,
  startingCapital,
}: {
  runId: number;
  startingCapital: number | null;
}): JSX.Element {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["backtest-equity", runId],
    queryFn: () => fetchBacktestEquity(runId),
  });
  if (isLoading) {
    return (
      <div className="text-muted-foreground flex items-center gap-2 px-5 py-6 text-sm">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading equity curve…
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="text-destructive px-5 py-6 text-sm">
        Failed to load equity curve.
      </div>
    );
  }
  return (
    <div className="px-5 py-4">
      <EquityChart points={data} startingCapital={startingCapital} />
    </div>
  );
}

function DrawdownPanel({ runId }: { runId: number }): JSX.Element {
  // Reuse the same query key as StrategyEquityPanel — TanStack dedupes the
  // network call when both panels render together.
  const { data, isLoading, isError } = useQuery({
    queryKey: ["backtest-equity", runId],
    queryFn: () => fetchBacktestEquity(runId),
  });
  if (isLoading) {
    return (
      <div className="text-muted-foreground flex items-center gap-2 px-1 py-3 text-sm">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading…
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="text-destructive px-1 py-3 text-sm">
        Failed to load equity series.
      </div>
    );
  }
  return <DrawdownChart points={data} />;
}

function ReturnDistributionPanel({ runId }: { runId: number }): JSX.Element {
  // Reuse the trades query — TradeDetail already populates this cache, so
  // by the time the panel renders we typically read straight from cache.
  const { data, isLoading, isError } = useQuery({
    queryKey: ["backtest-trades", runId],
    queryFn: () => fetchBacktestTrades(runId),
  });
  if (isLoading) {
    return (
      <div className="text-muted-foreground flex items-center gap-2 px-1 py-3 text-sm">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading…
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="text-destructive px-1 py-3 text-sm">
        Failed to load trades.
      </div>
    );
  }
  // One bar per closed trade with a realized $ P&L. Open trades and
  // legs without a realized P&L (csp_open, cc_open) are excluded.
  const values = data
    .filter((t) => t.realized_pnl != null)
    .map((t) => t.realized_pnl as number);
  return <ReturnHistogram values={values} />;
}

const STRATEGY_LEG_TYPES = [
  "csp_open",
  "csp_close",
  "csp_assigned",
  "csp_expired",
  "cc_open",
  "cc_close",
  "cc_assigned",
  "cc_expired",
  "share_sold",
] as const;

// Keys that should be rendered as money rather than raw numbers in the
// diagnostic table. Matched as substrings so e.g. ``premium_total_credit``
// and ``close_total_debit`` both pick up money formatting.
const MONEY_KEY_HINTS = [
  "premium_total",
  "premium_received",
  "buyback",
  "fees",
  "slippage_total",
  "close_total",
  "collateral",
  "cost_basis_total",
  "share_unrealized",
  "proceeds",
  "net",
];

const PERCENT_KEY_HINTS = ["delta_target", "filter_score"];

function isMoneyKey(key: string): boolean {
  return MONEY_KEY_HINTS.some((hint) => key.includes(hint));
}

function isPercentLikeKey(key: string): boolean {
  return PERCENT_KEY_HINTS.some((hint) => key.includes(hint));
}

function formatMetaValue(key: string, value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "number") {
    if (isMoneyKey(key)) return fmtMoney(value);
    if (isPercentLikeKey(key)) return value.toFixed(4);
    if (Number.isInteger(value)) return value.toString();
    return value.toFixed(4);
  }
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "true" : "false";
  return JSON.stringify(value);
}

function PnlBreakdownTable({
  breakdown,
}: {
  breakdown: Record<string, unknown>;
}): JSX.Element {
  const entries = Object.entries(breakdown);
  return (
    <div className="border-border bg-muted/40 rounded-md border">
      <table className="w-full text-sm">
        <tbody>
          {entries.map(([k, v], i) => {
            const isNet = k === "net";
            const num = typeof v === "number" ? v : null;
            return (
              <tr
                key={k}
                className={cn(
                  i < entries.length - 1 && "border-border/60 border-b",
                  isNet && "bg-muted/60 font-semibold",
                )}
              >
                <td className="text-muted-foreground px-3 py-1.5 font-mono text-xs uppercase">
                  {k}
                </td>
                <td
                  className={cn(
                    "px-3 py-1.5 text-right font-mono",
                    num != null &&
                      (num >= 0 ? "text-emerald-500" : "text-red-500"),
                  )}
                >
                  {num != null
                    ? `${num >= 0 ? "+" : ""}${fmtMoney(num)}`
                    : String(v)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TradeDetailDialog({
  trade,
  cycleTrades,
  onClose,
}: {
  trade: BacktestTradeOut;
  cycleTrades: BacktestTradeOut[];
  onClose: () => void;
}): JSX.Element {
  const meta = trade.meta ?? {};
  const breakdown = (meta.pnl_breakdown ?? null) as
    | Record<string, unknown>
    | null;
  const explanation = typeof meta.explanation === "string" ? meta.explanation : null;

  // Show all meta keys except the two we render specially (pnl_breakdown,
  // explanation) and ``lots`` (rendered as a sub-table when present).
  const diagnostic = Object.entries(meta).filter(
    ([k]) => k !== "pnl_breakdown" && k !== "explanation" && k !== "lots",
  );
  const lots = (meta.lots ?? null) as
    | Array<Record<string, unknown>>
    | null;

  const siblings = cycleTrades.filter((t) => t.id !== trade.id);

  return (
    <Dialog open onOpenChange={(o) => (o ? undefined : onClose())}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="font-mono">{trade.symbol}</span>
            <span className="text-muted-foreground">·</span>
            <span className="font-mono text-sm">{trade.leg_type}</span>
            {trade.cycle_id != null && (
              <Badge variant="outline" className="ml-auto">
                cycle #{trade.cycle_id}
              </Badge>
            )}
          </DialogTitle>
          <DialogDescription>
            {formatDate(trade.entry_date)}
            {trade.exit_date ? ` → ${formatDate(trade.exit_date)}` : " (open)"}
            {trade.outcome ? ` · ${trade.outcome}` : ""}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          {explanation && (
            <div className="border-border bg-muted/30 rounded-md border px-3 py-2 text-sm leading-relaxed">
              {explanation}
            </div>
          )}

          {breakdown && (
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider">
                P&L breakdown
              </h3>
              <PnlBreakdownTable breakdown={breakdown} />
              {trade.realized_pnl != null && (
                <div className="text-muted-foreground mt-2 text-xs">
                  Persisted realized_pnl on this row: {fmtMoney(trade.realized_pnl)}
                </div>
              )}
            </div>
          )}

          {diagnostic.length > 0 && (
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider">
                Diagnostic detail
              </h3>
              <div className="border-border rounded-md border">
                <table className="w-full text-sm">
                  <tbody>
                    {diagnostic.map(([k, v], i) => (
                      <tr
                        key={k}
                        className={cn(
                          i < diagnostic.length - 1 && "border-border/60 border-b",
                        )}
                      >
                        <td className="text-muted-foreground px-3 py-1.5 font-mono text-xs">
                          {k}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono">
                          {formatMetaValue(k, v)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {lots && lots.length > 0 && (
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider">
                Share lots delivered
              </h3>
              <div className="border-border rounded-md border">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-border/60 text-muted-foreground border-b text-xs uppercase">
                      <th className="px-3 py-1.5 text-left">Cycle</th>
                      <th className="px-3 py-1.5 text-right">Shares</th>
                      <th className="px-3 py-1.5 text-right">Cost basis</th>
                      <th className="px-3 py-1.5 text-left">Acquired</th>
                    </tr>
                  </thead>
                  <tbody>
                    {lots.map((lot, i) => (
                      <tr
                        key={i}
                        className={cn(
                          i < lots.length - 1 && "border-border/60 border-b",
                        )}
                      >
                        <td className="px-3 py-1.5 font-mono text-xs">
                          #{String(lot.cycle_id)}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono">
                          {String(lot.shares)}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono">
                          {typeof lot.cost_basis === "number"
                            ? `$${fmt(lot.cost_basis)}`
                            : String(lot.cost_basis)}
                        </td>
                        <td className="px-3 py-1.5">
                          {String(lot.acquired_date)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {trade.cycle_id != null && siblings.length > 0 && (
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider">
                Other legs in cycle #{trade.cycle_id}
              </h3>
              <div className="border-border rounded-md border">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-border/60 text-muted-foreground border-b text-xs uppercase">
                      <th className="px-3 py-1.5 text-left">Leg</th>
                      <th className="px-3 py-1.5 text-left">Entry</th>
                      <th className="px-3 py-1.5 text-left">Exit</th>
                      <th className="px-3 py-1.5 text-right">Strike</th>
                      <th className="px-3 py-1.5 text-right">P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {siblings.map((s, i) => (
                      <tr
                        key={s.id}
                        className={cn(
                          i < siblings.length - 1 && "border-border/60 border-b",
                        )}
                      >
                        <td className="px-3 py-1.5 font-mono text-xs">
                          {s.leg_type}
                        </td>
                        <td className="px-3 py-1.5">
                          {formatDate(s.entry_date)}
                        </td>
                        <td className="px-3 py-1.5">
                          {s.exit_date ? formatDate(s.exit_date) : "—"}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono">
                          {s.strike != null ? `$${fmt(s.strike)}` : "—"}
                        </td>
                        <td
                          className={cn(
                            "px-3 py-1.5 text-right font-mono",
                            s.realized_pnl != null &&
                              (s.realized_pnl >= 0
                                ? "text-emerald-500"
                                : "text-red-500"),
                          )}
                        >
                          {s.realized_pnl != null
                            ? `${s.realized_pnl >= 0 ? "+" : ""}${fmtMoney(s.realized_pnl)}`
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {!explanation && !breakdown && diagnostic.length === 0 && (
            <div className="text-muted-foreground text-sm">
              No diagnostic detail recorded for this trade. (Older runs from
              before the simulator captured per-leg context will not have it.)
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

type GroupBy = "trades" | "symbol";

interface SymbolGroup {
  symbol: string;
  tradeCount: number;
  wins: number;
  losses: number;
  totalPnl: number;
  firstEntry: string;
  lastExit: string | null;
}

function buildSymbolGroups(data: BacktestTradeOut[]): SymbolGroup[] {
  const map = new Map<string, SymbolGroup>();
  for (const t of data) {
    const g = map.get(t.symbol) ?? {
      symbol: t.symbol,
      tradeCount: 0,
      wins: 0,
      losses: 0,
      totalPnl: 0,
      firstEntry: t.entry_date,
      lastExit: t.exit_date,
    };
    g.tradeCount++;
    g.totalPnl += t.realized_pnl ?? 0;
    if (t.outcome === "win") g.wins++;
    if (t.outcome === "loss") g.losses++;
    if (t.entry_date < g.firstEntry) g.firstEntry = t.entry_date;
    if (t.exit_date != null && (g.lastExit == null || t.exit_date > g.lastExit))
      g.lastExit = t.exit_date;
    map.set(t.symbol, g);
  }
  return [...map.values()].sort((a, b) => b.totalPnl - a.totalPnl);
}

function SymbolGroupTable({ groups }: { groups: SymbolGroup[] }): JSX.Element {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Symbol</TableHead>
            <TableHead className="text-right">Trades</TableHead>
            <TableHead className="text-right">W / L</TableHead>
            <TableHead className="text-right">Win %</TableHead>
            <TableHead className="text-right">Total P&L</TableHead>
            <TableHead>First entry</TableHead>
            <TableHead>Last exit</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {groups.map((g) => {
            const scored = g.wins + g.losses;
            const winPct = scored > 0 ? (g.wins / scored) * 100 : null;
            return (
              <TableRow key={g.symbol}>
                <TableCell className="font-mono font-medium">{g.symbol}</TableCell>
                <TableCell className="text-right font-mono">{g.tradeCount}</TableCell>
                <TableCell className="text-right font-mono">
                  {scored > 0 ? `${g.wins} / ${g.losses}` : "—"}
                </TableCell>
                <TableCell className="text-right font-mono">
                  {winPct != null ? `${winPct.toFixed(0)}%` : "—"}
                </TableCell>
                <TableCell className="text-right">
                  <span
                    className={cn(
                      "font-mono font-medium",
                      g.totalPnl >= 0 ? "text-emerald-500" : "text-red-500",
                    )}
                  >
                    {g.totalPnl >= 0 ? "+" : ""}
                    {fmtMoney(g.totalPnl)}
                  </span>
                </TableCell>
                <TableCell>{formatDate(g.firstEntry)}</TableCell>
                <TableCell>
                  {g.lastExit ? formatDate(g.lastExit) : "—"}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function TradeDetail({
  runId,
  mode,
}: {
  runId: number;
  mode: BacktestMode;
}): JSX.Element {
  const [legFilter, setLegFilter] = useState<string>("");
  const [selectedTrade, setSelectedTrade] = useState<BacktestTradeOut | null>(
    null,
  );
  const [groupBy, setGroupBy] = useState<GroupBy>("trades");

  const { data, isLoading, isError } = useQuery({
    queryKey: ["backtest-trades", runId],
    queryFn: () => fetchBacktestTrades(runId),
  });

  const symbolGroups = useMemo(
    () => (data ? buildSymbolGroups(data) : []),
    [data],
  );

  if (isLoading) {
    return (
      <div className="text-muted-foreground flex items-center gap-2 px-5 py-6 text-sm">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading trades…
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="text-destructive px-5 py-6 text-sm">Failed to load trades.</div>
    );
  }
  if (data.length === 0) {
    return (
      <div className="text-muted-foreground px-5 py-6 text-sm">
        {mode === "filter"
          ? "No trades — no symbols passed the filter over this date range."
          : "No trades — the simulator never opened a position."}
      </div>
    );
  }

  const filtered = legFilter ? data.filter((t) => t.leg_type === legFilter) : data;

  const GroupByToggle = (
    <div className="flex items-center gap-1 rounded-md border border-border p-0.5">
      {(["trades", "symbol"] as GroupBy[]).map((g) => (
        <button
          key={g}
          type="button"
          onClick={() => setGroupBy(g)}
          className={cn(
            "rounded-sm px-2 py-0.5 text-xs capitalize transition-colors",
            groupBy === g
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {g === "symbol" ? "by symbol" : "trades"}
        </button>
      ))}
    </div>
  );

  if (mode === "filter") {
    return (
      <div>
        <div className="flex items-center gap-3 px-5 pt-3 pb-2">
          {GroupByToggle}
        </div>
        {groupBy === "symbol" ? (
          <SymbolGroupTable groups={symbolGroups} />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Symbol</TableHead>
                  <TableHead>Entry</TableHead>
                  <TableHead>Exit</TableHead>
                  <TableHead className="text-right">Entry $</TableHead>
                  <TableHead className="text-right">Exit $</TableHead>
                  <TableHead className="text-right">Return</TableHead>
                  <TableHead>Outcome</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((trade) => (
                  <TableRow key={trade.id}>
                    <TableCell className="font-mono font-medium">{trade.symbol}</TableCell>
                    <TableCell>{formatDate(trade.entry_date)}</TableCell>
                    <TableCell>{formatDate(trade.exit_date)}</TableCell>
                    <TableCell className="text-right font-mono">
                      ${fmt(trade.entry_price)}
                    </TableCell>
                    <TableCell className="text-right font-mono">
                      {trade.exit_price != null ? `$${fmt(trade.exit_price)}` : "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <ReturnText value={trade.realized_pnl_pct} />
                    </TableCell>
                    <TableCell>
                      {trade.outcome ? (
                        <Badge
                          variant={trade.outcome === "win" ? "success" : "destructive"}
                        >
                          {trade.outcome}
                        </Badge>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    );
  }

  // Strategy mode
  const presentLegTypes = STRATEGY_LEG_TYPES.filter((leg) =>
    data.some((t) => t.leg_type === leg),
  );

  const cycleTradesForSelected =
    selectedTrade != null && selectedTrade.cycle_id != null
      ? data.filter((t) => t.cycle_id === selectedTrade.cycle_id)
      : [];

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 px-5 pt-3">
        {GroupByToggle}
        {groupBy === "trades" && presentLegTypes.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-muted-foreground text-xs">Leg:</span>
            <button
              type="button"
              onClick={() => setLegFilter("")}
              className={cn(
                "rounded-md px-2 py-0.5 text-xs",
                legFilter === ""
                  ? "bg-primary text-primary-foreground"
                  : "border-border bg-background hover:bg-muted border",
              )}
            >
              all
            </button>
            {presentLegTypes.map((leg) => (
              <button
                key={leg}
                type="button"
                onClick={() => setLegFilter(leg)}
                className={cn(
                  "rounded-md px-2 py-0.5 font-mono text-xs",
                  legFilter === leg
                    ? "bg-primary text-primary-foreground"
                    : "border-border bg-background hover:bg-muted border",
                )}
              >
                {leg}
              </button>
            ))}
          </div>
        )}
      </div>
      {groupBy === "symbol" ? (
        <SymbolGroupTable groups={symbolGroups} />
      ) : (
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Symbol</TableHead>
              <TableHead>Leg</TableHead>
              <TableHead>Entry</TableHead>
              <TableHead>Exit</TableHead>
              <TableHead className="text-right">Strike</TableHead>
              <TableHead>Expires</TableHead>
              <TableHead className="text-right">Entry $</TableHead>
              <TableHead className="text-right">Exit $</TableHead>
              <TableHead className="text-right">P&L</TableHead>
              <TableHead>Outcome</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((trade) => (
              <TableRow
                key={trade.id}
                className="hover:bg-muted/50 cursor-pointer"
                title="Click for full simulator detail"
                onClick={() => setSelectedTrade(trade)}
              >
                <TableCell className="font-mono font-medium">{trade.symbol}</TableCell>
                <TableCell className="font-mono text-xs">{trade.leg_type}</TableCell>
                <TableCell>{formatDate(trade.entry_date)}</TableCell>
                <TableCell>{formatDate(trade.exit_date)}</TableCell>
                <TableCell className="text-right font-mono">
                  {trade.strike != null ? `$${fmt(trade.strike)}` : "—"}
                </TableCell>
                <TableCell>
                  {trade.expiration ? formatDate(trade.expiration) : "—"}
                </TableCell>
                <TableCell className="text-right font-mono">
                  ${fmt(trade.entry_price)}
                </TableCell>
                <TableCell className="text-right font-mono">
                  {trade.exit_price != null ? `$${fmt(trade.exit_price)}` : "—"}
                </TableCell>
                <TableCell className="text-right">
                  {trade.realized_pnl != null ? (
                    <span
                      className={cn(
                        "font-mono font-medium",
                        trade.realized_pnl >= 0 ? "text-emerald-500" : "text-red-500",
                      )}
                    >
                      {trade.realized_pnl >= 0 ? "+" : ""}
                      {fmtMoney(trade.realized_pnl)}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell>
                  {trade.outcome ? (
                    <span className="text-muted-foreground text-xs">{trade.outcome}</span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      )}
      {groupBy === "trades" && selectedTrade && (
        <TradeDetailDialog
          trade={selectedTrade}
          cycleTrades={cycleTradesForSelected}
          onClose={() => setSelectedTrade(null)}
        />
      )}
    </div>
  );
}

function ExpandedRunDetail({ run }: { run: BacktestRunOut }): JSX.Element {
  // Poll while running so the UI reflects the terminal state without a manual refresh.
  const isRunning = run.status === "running";
  const { data: liveRun } = useQuery({
    queryKey: ["backtest-run", run.id],
    queryFn: () => fetchBacktestRun(run.id),
    refetchInterval: isRunning ? 2000 : false,
    initialData: run,
  });
  const current = liveRun ?? run;

  return (
    <div className="space-y-4 px-5 py-4">
      {current.status === "failed" && current.error_message && (
        <div className="border-destructive/40 bg-destructive/10 text-destructive rounded-md border px-3 py-2 text-sm">
          {current.error_message}
        </div>
      )}
      {current.mode === "strategy" && current.status !== "running" && (
        <>
          <div>
            <h3 className="mb-2 text-sm font-semibold">Equity curve</h3>
            <StrategyEquityPanel
              runId={current.id}
              startingCapital={current.starting_capital}
            />
          </div>
          {current.status === "completed" && (
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <h3 className="mb-2 text-sm font-semibold">Underwater (drawdown)</h3>
                <DrawdownPanel runId={current.id} />
              </div>
              <div>
                <h3 className="mb-2 text-sm font-semibold">
                  Per-cycle realized P&L distribution
                </h3>
                <ReturnDistributionPanel runId={current.id} />
              </div>
            </div>
          )}
        </>
      )}
      {current.status === "running" ? (
        <div className="text-muted-foreground flex items-center gap-2 text-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          Backtest is running. Polling every 2s for completion…
        </div>
      ) : (
        <div>
          <div className="mb-2 flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold">Trades</h3>
            {current.status === "completed" && current.trade_count > 0 && (
              <a
                href={backtestTradesCsvUrl(current.id)}
                download
                title="Download trades + run metadata as CSV (paste into Claude or a spreadsheet to audit)"
                className="text-muted-foreground hover:text-foreground border-border bg-background hover:bg-muted inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium"
              >
                <Download className="h-3.5 w-3.5" />
                Export CSV
              </a>
            )}
          </div>
          <div className="border-border bg-background rounded-md border">
            <TradeDetail runId={current.id} mode={current.mode} />
          </div>
        </div>
      )}
    </div>
  );
}

function useRunRowState(run: BacktestRunOut) {
  const qc = useQueryClient();
  const isRunning = run.status === "running";
  const { data: liveRun } = useQuery({
    queryKey: ["backtest-run-list", run.id],
    queryFn: () => fetchBacktestRun(run.id),
    refetchInterval: isRunning ? 2000 : false,
    initialData: run,
  });
  const current = liveRun ?? run;
  const del = useMutation({
    mutationFn: () => deleteBacktestRun(current.id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["backtest-runs"] });
    },
  });
  const headlineReturn =
    current.mode === "strategy" ? current.total_return_pct : current.mean_return_pct;
  const headlineRight =
    current.mode === "strategy"
      ? current.cycles_completed != null
        ? `${current.cycles_completed} cycles`
        : "—"
      : current.win_rate != null
        ? `${(current.win_rate * 100).toFixed(0)}% win`
        : "—";
  return { current, del, headlineReturn, headlineRight };
}

function RunRow({
  run,
  selection,
}: {
  run: BacktestRunOut;
  selection: SelectionApi;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const { current, del, headlineReturn, headlineRight } = useRunRowState(run);
  const selectable = selection.isSelectable(current);
  const checked = selection.isSelected(current.id);

  return (
    <>
      <TableRow
        className="cursor-pointer select-none"
        onClick={() => setExpanded((v) => !v)}
      >
        <TableCell className="w-9 pr-0" onClick={(e) => e.stopPropagation()}>
          <input
            type="checkbox"
            aria-label={`Select run ${current.id} for comparison`}
            disabled={!selectable && !checked}
            checked={checked}
            onChange={(e) => selection.toggle(current.id, e.target.checked)}
            className="h-4 w-4 cursor-pointer accent-primary"
          />
        </TableCell>
        <TableCell>
          <ModeBadge mode={current.mode} />
        </TableCell>
        <TableCell>
          <span className="font-medium">
            {current.config_name ?? `Config #${current.config_id}`}
          </span>
        </TableCell>
        <TableCell className="text-sm">
          {formatDate(current.start_date)} – {formatDate(current.end_date)}
        </TableCell>
        <TableCell>
          <StatusBadge status={current.status} />
        </TableCell>
        <TableCell className="text-right font-mono">{current.trade_count}</TableCell>
        <TableCell className="text-right">
          <ReturnText value={headlineReturn} />
        </TableCell>
        <TableCell className="text-muted-foreground text-right text-xs">
          {headlineRight}
        </TableCell>
        <TableCell className="text-muted-foreground text-xs">
          {formatDateTime(current.created_at)}
        </TableCell>
        <TableCell>
          <div className="flex items-center justify-end gap-2">
            {current.status === "completed" && current.trade_count > 0 && (
              <a
                href={backtestTradesCsvUrl(current.id)}
                download
                onClick={(e) => e.stopPropagation()}
                title="Download trades + run metadata as CSV"
                className="text-muted-foreground hover:text-foreground inline-flex h-7 w-7 items-center justify-center rounded-md hover:bg-muted"
              >
                <Download className="h-3.5 w-3.5" />
              </a>
            )}
            <Button
              size="sm"
              variant="ghost"
              className="text-destructive hover:text-destructive h-7 w-7 p-0"
              title="Delete this run"
              disabled={del.isPending}
              onClick={(e) => {
                e.stopPropagation();
                del.mutate();
              }}
            >
              {del.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Trash2 className="h-3.5 w-3.5" />
              )}
            </Button>
            {expanded ? (
              <ChevronUp className="text-muted-foreground h-4 w-4" />
            ) : (
              <ChevronDown className="text-muted-foreground h-4 w-4" />
            )}
          </div>
        </TableCell>
      </TableRow>
      {expanded && (
        <TableRow>
          <TableCell colSpan={10} className="bg-muted/30 p-0">
            <ExpandedRunDetail run={current} />
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

interface SelectionApi {
  isSelected: (id: number) => boolean;
  isSelectable: (run: BacktestRunOut) => boolean;
  toggle: (id: number, next: boolean) => void;
  selectedIds: number[];
  max: number;
}

function RunMobileCard({ run }: { run: BacktestRunOut }): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const { current, del, headlineReturn, headlineRight } = useRunRowState(run);

  return (
    <li className="px-1 py-3">
      <div
        onClick={() => setExpanded((v) => !v)}
        className="active:bg-accent/40 -mx-1 cursor-pointer px-1 transition-colors"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <ModeBadge mode={current.mode} />
              <StatusBadge status={current.status} />
            </div>
            <div className="mt-1 truncate text-sm font-medium">
              {current.config_name ?? `Config #${current.config_id}`}
            </div>
            <div className="text-muted-foreground mt-0.5 font-mono text-[11px]">
              {formatDate(current.start_date)} – {formatDate(current.end_date)}
            </div>
          </div>
          <div className="shrink-0 text-right">
            <ReturnText value={headlineReturn} />
            <div className="text-muted-foreground text-[10px] uppercase tracking-wider">
              return
            </div>
          </div>
        </div>
        <div className="mt-3 grid grid-cols-3 gap-2">
          <div className="bg-muted/30 rounded-md px-2 py-1.5">
            <div className="text-muted-foreground text-[10px] font-medium uppercase tracking-wider">
              Trades
            </div>
            <div className="font-mono text-sm">{current.trade_count}</div>
          </div>
          <div className="bg-muted/30 rounded-md px-2 py-1.5">
            <div className="text-muted-foreground text-[10px] font-medium uppercase tracking-wider">
              Detail
            </div>
            <div className="font-mono text-xs">{headlineRight}</div>
          </div>
          <div className="bg-muted/30 rounded-md px-2 py-1.5">
            <div className="text-muted-foreground text-[10px] font-medium uppercase tracking-wider">
              Ran
            </div>
            <div className="font-mono text-xs">{formatDateTime(current.created_at)}</div>
          </div>
        </div>
        <div className="mt-2 flex items-center justify-end gap-2">
          {current.status === "completed" && current.trade_count > 0 && (
            <a
              href={backtestTradesCsvUrl(current.id)}
              download
              onClick={(e) => e.stopPropagation()}
              title="Download trades + run metadata as CSV"
              className="text-muted-foreground hover:text-foreground inline-flex h-7 w-7 items-center justify-center rounded-md hover:bg-muted"
            >
              <Download className="h-3.5 w-3.5" />
            </a>
          )}
          <Button
            size="sm"
            variant="ghost"
            className="text-destructive hover:text-destructive h-7 w-7 p-0"
            title="Delete this run"
            disabled={del.isPending}
            onClick={(e) => {
              e.stopPropagation();
              del.mutate();
            }}
          >
            {del.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Trash2 className="h-3.5 w-3.5" />
            )}
          </Button>
          {expanded ? (
            <ChevronUp className="text-muted-foreground h-4 w-4" />
          ) : (
            <ChevronDown className="text-muted-foreground h-4 w-4" />
          )}
        </div>
      </div>
      {expanded && (
        <div className="bg-muted/30 -mx-1 mt-2 rounded-md">
          <ExpandedRunDetail run={current} />
        </div>
      )}
    </li>
  );
}

const COMPARE_MAX_RUNS = 3;

function RunsCard(): JSX.Element {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["backtest-runs"],
    queryFn: fetchBacktestRuns,
    // Refresh the list while there's any running row so freshly-completed runs surface.
    refetchInterval: (query) => {
      const rows = query.state.data;
      if (Array.isArray(rows) && rows.some((r) => r.status === "running")) {
        return 2000;
      }
      return false;
    },
  });

  const [selectedIds, setSelectedIds] = useState<number[]>([]);

  const validRuns = useMemo(() => {
    if (!data) return new Map<number, BacktestRunOut>();
    return new Map(
      data
        .filter((r) => r.mode === "strategy" && r.status === "completed")
        .map((r) => [r.id, r]),
    );
  }, [data]);

  // Drop selections when the underlying run is gone (deleted, mode changed
  // upstream, etc.) so the toolbar doesn't lie about what will be sent.
  const effectiveSelection = useMemo(
    () => selectedIds.filter((id) => validRuns.has(id)),
    [selectedIds, validRuns],
  );

  const selectionApi: SelectionApi = useMemo(
    () => ({
      max: COMPARE_MAX_RUNS,
      selectedIds: effectiveSelection,
      isSelected: (id) => effectiveSelection.includes(id),
      isSelectable: (run) =>
        run.mode === "strategy" && run.status === "completed",
      toggle: (id, next) => {
        setSelectedIds((prev) => {
          if (next) {
            if (prev.includes(id)) return prev;
            if (prev.length >= COMPARE_MAX_RUNS) return prev;
            return [...prev, id];
          }
          return prev.filter((x) => x !== id);
        });
      },
    }),
    [effectiveSelection],
  );

  const compareHref =
    effectiveSelection.length >= 2
      ? `/backtest/compare?ids=${effectiveSelection.join(",")}`
      : null;

  return (
    <Card>
      <CardHeader className="px-3 sm:px-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle>Past runs</CardTitle>
          <div className="flex items-center gap-3">
            {effectiveSelection.length > 0 && (
              <span className="text-muted-foreground text-xs">
                {effectiveSelection.length} of {COMPARE_MAX_RUNS} selected
              </span>
            )}
            {compareHref ? (
              <Link
                to={compareHref}
                className={buttonVariants({ variant: "default", size: "sm" })}
              >
                <GitCompareArrows className="mr-2 h-3.5 w-3.5" />
                Compare selected
              </Link>
            ) : (
              <Button size="sm" variant="secondary" disabled>
                <GitCompareArrows className="mr-2 h-3.5 w-3.5" />
                Compare selected
              </Button>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="px-0">
        {isLoading ? (
          <div className="text-muted-foreground flex items-center gap-2 px-5 py-8 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading…
          </div>
        ) : isError ? (
          <div className="text-destructive px-5 py-8 text-sm">
            Failed to load runs.
          </div>
        ) : !data || data.length === 0 ? (
          <div className="text-muted-foreground px-5 py-8 text-center text-sm">
            No runs yet. Use the form above to run your first backtest.
          </div>
        ) : (
          <>
            <ul className="divide-border/50 mx-3 divide-y md:hidden">
              {data.map((run) => (
                <RunMobileCard key={run.id} run={run} />
              ))}
            </ul>
            <div className="hidden md:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-9"></TableHead>
                    <TableHead>Mode</TableHead>
                    <TableHead>Config</TableHead>
                    <TableHead>Period</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Trades</TableHead>
                    <TableHead className="text-right">Return</TableHead>
                    <TableHead className="text-right">Detail</TableHead>
                    <TableHead>Ran at</TableHead>
                    <TableHead></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.map((run) => (
                    <RunRow key={run.id} run={run} selection={selectionApi} />
                  ))}
                </TableBody>
              </Table>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

type NumericStrategyKey =
  | "starting_capital"
  | "max_concurrent_positions"
  | "dte_target"
  | "delta_target"
  | "profit_take_pct"
  | "manage_dte"
  | "fee_per_contract"
  | "slippage_per_share";

const STRATEGY_DEFAULTS: Record<NumericStrategyKey, number> = {
  starting_capital: 10000,
  max_concurrent_positions: 5,
  dte_target: 30,
  delta_target: 0.3,
  profit_take_pct: 0.5,
  manage_dte: 21,
  fee_per_contract: 0.65,
  slippage_per_share: 0.02,
};

type StrategyFormState = Record<NumericStrategyKey, string>;

function strategyDefaultsAsForm(): StrategyFormState {
  const out = {} as StrategyFormState;
  (Object.keys(STRATEGY_DEFAULTS) as NumericStrategyKey[]).forEach((k) => {
    out[k] = String(STRATEGY_DEFAULTS[k]);
  });
  return out;
}

const COVERAGE_OK_THRESHOLD = 0.95;

function CoverageBanner({
  coverage,
}: {
  coverage: BacktestCoverageOut;
}): JSX.Element | null {
  if (coverage.symbol_day_pairs_expected === 0) return null;

  const pct = coverage.coverage_pct;
  const ok = pct >= COVERAGE_OK_THRESHOLD;
  const pctText = `${(pct * 100).toFixed(0)}%`;
  const missingCount = coverage.symbols_missing.length;

  if (ok && missingCount === 0) {
    return (
      <p className="mt-2 text-xs text-emerald-600 dark:text-emerald-400">
        Real-chain coverage: {pctText} of (symbol × trading-day) pairs.
      </p>
    );
  }

  const sampleMissing = coverage.symbols_missing.slice(0, 5).join(", ");
  const moreSuffix =
    missingCount > 5 ? ` (+${missingCount - 5} more)` : "";
  return (
    <div
      className={cn(
        "mt-2 rounded-md border px-3 py-2 text-xs",
        "border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-200",
      )}
    >
      <p className="font-medium">
        Partial real-chain coverage: {pctText} of (symbol × trading-day) pairs.
      </p>
      <p className="mt-1">
        Missing days fall back to synthetic Black-Scholes pricing per-row.
        {missingCount > 0 && (
          <>
            {" "}
            {missingCount} symbol{missingCount === 1 ? "" : "s"} have no
            historical chains in this window: {sampleMissing}
            {moreSuffix}.
          </>
        )}
        {coverage.first_uncovered_day && (
          <>
            {" "}
            First uncovered day: <code>{coverage.first_uncovered_day}</code>.
          </>
        )}
      </p>
      <p className="mt-1 text-muted-foreground">
        Backfill with{" "}
        <code>
          python -m ingestion.options_history --start {coverage.start} --end{" "}
          {coverage.end}
        </code>
        .
      </p>
    </div>
  );
}

function RunLauncherCard(): JSX.Element {
  const qc = useQueryClient();

  const { data: configs } = useQuery({
    queryKey: ["screener-configs"],
    queryFn: () => fetchScreenerConfigs(),
  });

  const [mode, setMode] = useState<BacktestMode>("filter");
  const [configId, setConfigId] = useState<string>("");
  const [startDate, setStartDate] = useState<string>("");
  const [endDate, setEndDate] = useState<string>("");
  const [forwardDays, setForwardDays] = useState<string>("30");
  const [symbols, setSymbols] = useState<string>("");
  const [strategyForm, setStrategyForm] = useState<StrategyFormState>(
    strategyDefaultsAsForm(),
  );
  const [holdLosersToExpiry, setHoldLosersToExpiry] = useState<boolean>(false);
  const [useRealChain, setUseRealChain] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const symbolsForCoverage = useMemo<string[] | undefined>(() => {
    const trimmed = symbols.trim();
    if (!trimmed) return undefined;
    const parsed = trimmed
      .split(",")
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    return parsed.length > 0 ? parsed : undefined;
  }, [symbols]);

  // Only query coverage when the form is filled out enough to be meaningful.
  // Strategy mode only — filter mode doesn't read options_historical.
  const coverageEnabled =
    mode === "strategy" &&
    useRealChain &&
    !!startDate &&
    !!endDate &&
    endDate > startDate;
  const coverageQuery = useQuery({
    queryKey: ["backtest-coverage", startDate, endDate, symbolsForCoverage ?? null],
    queryFn: () =>
      fetchBacktestCoverage({
        start: startDate,
        end: endDate,
        symbols: symbolsForCoverage,
      }),
    enabled: coverageEnabled,
    staleTime: 30_000,
  });

  const run = useMutation({
    mutationFn: (input: BacktestRunIn) => runBacktest(input),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["backtest-runs"] });
      setError(null);
    },
    onError: (err) => {
      if (err instanceof ApiError && typeof err.detail === "string") {
        setError(err.detail);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("Backtest failed.");
      }
    },
  });

  const submit = (): void => {
    const id = parseInt(configId, 10);
    if (!configId || isNaN(id)) {
      setError("Select a config.");
      return;
    }
    if (!startDate || !endDate) {
      setError("Start and end dates are required.");
      return;
    }
    if (endDate <= startDate) {
      setError("End date must be after start date.");
      return;
    }
    const symbolList = symbols.trim()
      ? symbols
          .split(",")
          .map((s) => s.trim().toUpperCase())
          .filter(Boolean)
      : null;

    const payload: BacktestRunIn = {
      mode,
      config_id: id,
      start_date: startDate,
      end_date: endDate,
      symbols: symbolList,
    };

    if (mode === "filter") {
      const fd = parseInt(forwardDays, 10);
      if (isNaN(fd) || fd < 1 || fd > 252) {
        setError("Forward days must be between 1 and 252.");
        return;
      }
      payload.forward_days = fd;
    } else {
      const sp: StrategyParamsIn = {};
      for (const key of Object.keys(STRATEGY_DEFAULTS) as NumericStrategyKey[]) {
        const raw = strategyForm[key];
        const parsed = parseFloat(raw);
        if (raw === "" || isNaN(parsed)) {
          setError(`${key} must be numeric.`);
          return;
        }
        sp[key] = parsed;
      }
      if (sp.starting_capital! <= 0) {
        setError("Starting capital must be > 0.");
        return;
      }
      if (sp.delta_target! <= 0 || sp.delta_target! >= 1) {
        setError("Delta target must be between 0 and 1.");
        return;
      }
      if (sp.profit_take_pct! <= 0 || sp.profit_take_pct! > 1) {
        setError("Profit-take % must be between 0 and 1.");
        return;
      }
      sp.hold_losers_to_expiry = holdLosersToExpiry;
      sp.use_real_chain = useRealChain;
      payload.strategy_params = sp;
    }

    setError(null);
    run.mutate(payload);
  };

  return (
    <Card>
      <CardHeader className="px-3 sm:px-5">
        <CardTitle>Run a backtest</CardTitle>
      </CardHeader>
      <CardContent className="px-3 pb-3 sm:px-5 sm:pb-5">
        <div className="border-border mb-5 inline-flex rounded-md border p-0.5">
          {(["filter", "strategy"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={cn(
                "rounded-sm px-3 py-1 text-sm font-medium capitalize transition-colors",
                mode === m
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {m}
            </button>
          ))}
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div className="space-y-1.5">
            <label className="text-sm font-medium" htmlFor="bt-config">
              Config
            </label>
            <select
              id="bt-config"
              value={configId}
              onChange={(e) => setConfigId(e.target.value)}
              className="border-input bg-background focus-visible:ring-ring flex h-9 w-full rounded-md border px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <option value="">Select a config…</option>
              {configs?.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1.5">
            <label className="text-sm font-medium" htmlFor="bt-start">
              Start date
            </label>
            <Input
              id="bt-start"
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-sm font-medium" htmlFor="bt-end">
              End date
            </label>
            <Input
              id="bt-end"
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
            />
          </div>

          {mode === "filter" && (
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor="bt-fwd">
                Forward days
              </label>
              <Input
                id="bt-fwd"
                type="number"
                min={1}
                max={252}
                value={forwardDays}
                onChange={(e) => setForwardDays(e.target.value)}
              />
            </div>
          )}

          <div className="space-y-1.5 sm:col-span-2">
            <label className="text-sm font-medium" htmlFor="bt-symbols">
              Symbols{" "}
              <span className="text-muted-foreground font-normal">
                (optional, comma-separated)
              </span>
            </label>
            <Input
              id="bt-symbols"
              type="text"
              placeholder="AAPL, MSFT, TSLA"
              value={symbols}
              onChange={(e) => setSymbols(e.target.value)}
            />
          </div>
        </div>

        {mode === "strategy" && (
          <div className="mt-5">
            <h3 className="text-muted-foreground mb-2 text-xs font-semibold uppercase tracking-wider">
              Strategy parameters
            </h3>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {(
                [
                  ["starting_capital", "Starting capital ($)", "1"],
                  ["max_concurrent_positions", "Max concurrent positions", "1"],
                  ["dte_target", "DTE target (days)", "1"],
                  ["delta_target", "Delta target", "0.01"],
                  ["profit_take_pct", "Profit-take %", "0.05"],
                  ["manage_dte", "Manage DTE (days)", "1"],
                  ["fee_per_contract", "Fee/contract ($)", "0.05"],
                  ["slippage_per_share", "Slippage/share ($)", "0.01"],
                ] as Array<[keyof StrategyFormState, string, string]>
              ).map(([key, label, step]) => (
                <div key={key} className="space-y-1.5">
                  <label className="text-sm font-medium" htmlFor={`bt-sp-${key}`}>
                    {label}
                  </label>
                  <Input
                    id={`bt-sp-${key}`}
                    type="number"
                    step={step}
                    value={strategyForm[key]}
                    onChange={(e) =>
                      setStrategyForm((prev) => ({ ...prev, [key]: e.target.value }))
                    }
                  />
                </div>
              ))}
            </div>
            <div className="mt-4">
              <Checkbox
                id="bt-sp-real-chain"
                checked={useRealChain}
                onChange={(e) => setUseRealChain(e.target.checked)}
                label="Use real option chains (recommended)"
              />
              <p className="text-muted-foreground mt-1 text-xs">
                Reads strike + price from <code>options_historical</code>; falls
                back to synthetic Black-Scholes per-row when a strike is
                missing. Uncheck to force pure synthetic pricing.
              </p>
              {useRealChain && coverageQuery.data && (
                <CoverageBanner coverage={coverageQuery.data} />
              )}
            </div>
            <div className="mt-4">
              <Checkbox
                id="bt-sp-hold-losers"
                checked={holdLosersToExpiry}
                onChange={(e) => setHoldLosersToExpiry(e.target.checked)}
                label="Hold losers to expiry (true wheel)"
              />
              <p className="text-muted-foreground mt-1 text-xs">
                Skip the manage-DTE buy-back when it would realize a loss. ITM
                puts ride to assignment, ITM calls deliver shares at the
                cost-basis-floored strike. Pair with the True Wheel preset.
              </p>
            </div>
          </div>
        )}

        <div className="mt-5 flex items-center gap-4">
          <Button onClick={submit} disabled={run.isPending}>
            {run.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Play className="mr-2 h-4 w-4" />
            )}
            {run.isPending ? "Launching…" : "Run backtest"}
          </Button>
          {mode === "strategy" && (
            <p className="text-muted-foreground text-xs">
              Strategy runs simulate the wheel day-by-day. Real option chains
              from <code>options_historical</code> are used when available
              (synthetic Black-Scholes per-row fallback). Long windows take a
              minute.
            </p>
          )}
        </div>

        {error && <p className="text-destructive mt-3 text-sm">{error}</p>}
      </CardContent>
    </Card>
  );
}

export function Backtest(): JSX.Element {
  const description = useMemo(
    () =>
      "Filter mode replays a screener config and measures forward stock returns; strategy mode simulates the full wheel (cash-secured put → covered call) with synthetic option pricing.",
    [],
  );
  return (
    <div className="space-y-8">
      <header className="space-y-1.5">
        <p className="text-primary text-xs font-semibold uppercase tracking-widest">
          Analysis
        </p>
        <h1 className="flex items-center gap-2.5 text-3xl font-semibold tracking-tight">
          <FlaskConical className="h-7 w-7" />
          Backtest
        </h1>
        <p className="text-muted-foreground max-w-2xl text-sm">{description}</p>
      </header>

      <RunLauncherCard />
      <RunsCard />
    </div>
  );
}
