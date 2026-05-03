import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, FlaskConical, Loader2, Play, Trash2 } from "lucide-react";
import {
  deleteBacktestRun,
  fetchBacktestRuns,
  fetchBacktestTrades,
  fetchScreenerConfigs,
  runBacktest,
} from "@/api/client";
import type { BacktestRunIn, BacktestRunOut } from "@/api/types";
import { ApiError } from "@/api/client";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/Table";
import { formatDate, formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

function fmt(value: number | null | undefined, digits = 2): string {
  if (value == null) return "—";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function ReturnBadge({ value }: { value: number | null }): JSX.Element {
  if (value == null) return <span className="text-muted-foreground">—</span>;
  const positive = value >= 0;
  return (
    <span className={cn("font-mono font-medium", positive ? "text-emerald-600" : "text-red-500")}>
      {positive ? "+" : ""}
      {fmt(value)}%
    </span>
  );
}

function WinRateBadge({ value }: { value: number | null }): JSX.Element {
  if (value == null) return <span className="text-muted-foreground">—</span>;
  const pct = value * 100;
  return (
    <Badge variant={pct >= 50 ? "success" : "warning"}>{fmt(pct)}%</Badge>
  );
}

function TradeDetail({ runId }: { runId: number }): JSX.Element {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["backtest-trades", runId],
    queryFn: () => fetchBacktestTrades(runId),
  });

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
        No trades — no symbols passed the filter over this date range.
      </div>
    );
  }

  return (
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
        {data.map((trade) => (
          <TableRow key={trade.id}>
            <TableCell className="font-mono font-medium">{trade.symbol}</TableCell>
            <TableCell>{formatDate(trade.entry_date)}</TableCell>
            <TableCell>{formatDate(trade.exit_date)}</TableCell>
            <TableCell className="text-right font-mono">${fmt(trade.entry_price)}</TableCell>
            <TableCell className="text-right font-mono">
              {trade.exit_price != null ? `$${fmt(trade.exit_price)}` : "—"}
            </TableCell>
            <TableCell className="text-right">
              <ReturnBadge value={trade.realized_pnl_pct != null ? trade.realized_pnl_pct * 100 : null} />
            </TableCell>
            <TableCell>
              {trade.outcome ? (
                <Badge variant={trade.outcome === "win" ? "success" : "destructive"}>
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
  );
}

function RunRow({ run }: { run: BacktestRunOut }): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const qc = useQueryClient();

  const del = useMutation({
    mutationFn: () => deleteBacktestRun(run.id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["backtest-runs"] });
    },
  });

  const forwardDays =
    run.params_json && typeof run.params_json["forward_days"] === "number"
      ? (run.params_json["forward_days"] as number)
      : null;

  return (
    <>
      <TableRow
        className="cursor-pointer select-none"
        onClick={() => setExpanded((v) => !v)}
      >
        <TableCell>
          <span className="font-medium">{run.config_name ?? `Config #${run.config_id}`}</span>
        </TableCell>
        <TableCell className="text-sm">
          {formatDate(run.start_date)} – {formatDate(run.end_date)}
        </TableCell>
        <TableCell className="text-center">{forwardDays ?? "—"}</TableCell>
        <TableCell className="text-right font-mono">{run.trade_count}</TableCell>
        <TableCell className="text-right">
          <WinRateBadge value={run.win_rate} />
        </TableCell>
        <TableCell className="text-right">
          <ReturnBadge value={run.mean_return_pct} />
        </TableCell>
        <TableCell className="text-muted-foreground text-xs">{formatDateTime(run.created_at)}</TableCell>
        <TableCell>
          <div className="flex items-center justify-end gap-2">
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
          <TableCell colSpan={8} className="bg-muted/30 p-0">
            <TradeDetail runId={run.id} />
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

function RunsCard(): JSX.Element {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["backtest-runs"],
    queryFn: fetchBacktestRuns,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Past runs</CardTitle>
      </CardHeader>
      <CardContent className="px-0">
        {isLoading ? (
          <div className="text-muted-foreground flex items-center gap-2 px-5 py-8 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading…
          </div>
        ) : isError ? (
          <div className="text-destructive px-5 py-8 text-sm">Failed to load runs.</div>
        ) : !data || data.length === 0 ? (
          <div className="text-muted-foreground px-5 py-8 text-center text-sm">
            No runs yet. Use the form above to run your first backtest.
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Config</TableHead>
                <TableHead>Period</TableHead>
                <TableHead className="text-center">Fwd days</TableHead>
                <TableHead className="text-right">Trades</TableHead>
                <TableHead className="text-right">Win rate</TableHead>
                <TableHead className="text-right">Mean return</TableHead>
                <TableHead>Ran at</TableHead>
                <TableHead></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((run) => (
                <RunRow key={run.id} run={run} />
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function RunLauncherCard(): JSX.Element {
  const qc = useQueryClient();

  const { data: configs } = useQuery({
    queryKey: ["screener-configs"],
    queryFn: () => fetchScreenerConfigs(),
  });

  const [configId, setConfigId] = useState<string>("");
  const [startDate, setStartDate] = useState<string>("");
  const [endDate, setEndDate] = useState<string>("");
  const [forwardDays, setForwardDays] = useState<string>("30");
  const [symbols, setSymbols] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

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
    const fd = parseInt(forwardDays, 10);
    if (isNaN(fd) || fd < 1 || fd > 252) {
      setError("Forward days must be between 1 and 252.");
      return;
    }
    const symbolList =
      symbols.trim()
        ? symbols
            .split(",")
            .map((s) => s.trim().toUpperCase())
            .filter(Boolean)
        : null;

    setError(null);
    run.mutate({
      config_id: id,
      start_date: startDate,
      end_date: endDate,
      forward_days: fd,
      symbols: symbolList,
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Run a backtest</CardTitle>
      </CardHeader>
      <CardContent>
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

          <div className="space-y-1.5 sm:col-span-2">
            <label className="text-sm font-medium" htmlFor="bt-symbols">
              Symbols{" "}
              <span className="text-muted-foreground font-normal">(optional, comma-separated)</span>
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

        <div className="mt-5 flex items-center gap-4">
          <Button onClick={submit} disabled={run.isPending}>
            {run.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Play className="mr-2 h-4 w-4" />
            )}
            {run.isPending ? "Running…" : "Run backtest"}
          </Button>
          {run.isPending && (
            <p className="text-muted-foreground text-xs">
              This may take a minute for long date ranges.
            </p>
          )}
        </div>

        {error && (
          <p className="text-destructive mt-3 text-sm">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}

export function Backtest(): JSX.Element {
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
        <p className="text-muted-foreground max-w-2xl text-sm">
          Replay a screener config across history and measure forward stock returns for
          each filter pass.
        </p>
      </header>

      <RunLauncherCard />
      <RunsCard />
    </div>
  );
}
