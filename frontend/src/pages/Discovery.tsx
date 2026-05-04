import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Plus, Telescope } from "lucide-react";
import {
  createTicker,
  fetchScreenerConfigs,
  fetchScreenerResults,
  fetchTickers,
} from "@/api/client";
import type { ScreenerResultRow } from "@/api/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/Table";
import { formatDate, formatNumber, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

function annualizedColor(value: number | null): string {
  if (value === null) return "text-muted-foreground";
  if (value >= 0.25) return "text-emerald-300 font-semibold";
  if (value >= 0.15) return "text-emerald-400";
  if (value >= 0.1) return "text-yellow-300";
  return "text-muted-foreground";
}

function SectorPill({ sector }: { sector: string | null }): JSX.Element {
  return (
    <span className="bg-accent/60 text-muted-foreground rounded px-1.5 py-0.5 text-[11px]">
      {sector ?? "—"}
    </span>
  );
}

interface AddButtonProps {
  symbol: string;
  alreadyWatchlist: boolean;
}

function AddButton({ symbol, alreadyWatchlist }: AddButtonProps): JSX.Element {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => createTicker({ symbol }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["tickers"] });
    },
  });

  if (alreadyWatchlist) {
    return (
      <span className="text-muted-foreground text-xs italic">In watchlist</span>
    );
  }

  return (
    <button
      type="button"
      onClick={() => mutation.mutate()}
      disabled={mutation.isPending || mutation.isSuccess}
      className={cn(
        "flex items-center gap-1 rounded px-2 py-1 text-xs font-medium transition-colors",
        mutation.isSuccess
          ? "bg-emerald-500/20 text-emerald-300 cursor-default"
          : "bg-primary/10 text-primary hover:bg-primary/20 disabled:opacity-50",
      )}
    >
      <Plus className="h-3 w-3" />
      {mutation.isSuccess ? "Added" : mutation.isPending ? "Adding…" : "Add"}
    </button>
  );
}

export function Discovery(): JSX.Element {
  const [configId, setConfigId] = useState<number | null>(null);

  const { data: configs = [] } = useQuery({
    queryKey: ["screener-configs"],
    queryFn: () => fetchScreenerConfigs(true),
  });

  const { data: tickers = [] } = useQuery({
    queryKey: ["tickers"],
    queryFn: () => fetchTickers(true),
  });

  const watchlistSymbols = new Set(
    tickers.filter((t) => t.ticker_source === "watchlist").map((t) => t.symbol),
  );

  const effectiveConfigId = configId ?? configs[0]?.id ?? null;

  const { data, isLoading, isError } = useQuery({
    queryKey: ["screener-results", "universe", effectiveConfigId],
    queryFn: () =>
      fetchScreenerResults({
        configId: effectiveConfigId,
        passedOnly: true,
        tickerSource: "universe",
        limit: 100,
      }),
    enabled: effectiveConfigId !== null,
  });

  const rows: ScreenerResultRow[] = data?.rows ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <Telescope className="text-primary h-6 w-6" />
            Discovery
          </h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Blue-chip option premium opportunities from the S&P 100 universe, ranked by
            annualized return.
          </p>
        </div>

        {configs.length > 1 && (
          <select
            value={configId ?? ""}
            onChange={(e) => setConfigId(e.target.value ? Number(e.target.value) : null)}
            className="border-border bg-card text-foreground shrink-0 rounded-md border px-3 py-1.5 text-sm"
          >
            {configs.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        )}
      </div>

      {data && (
        <div className="text-muted-foreground text-xs">
          Results as of {formatDate(data.date)} · {rows.length} passed
          {data.config_name && ` · ${data.config_name}`}
        </div>
      )}

      <Card>
        <CardHeader className="px-3 pb-2 sm:px-5">
          <CardTitle className="text-base">Premium Opportunities</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading && (
            <div className="text-muted-foreground px-6 py-10 text-center text-sm">Loading…</div>
          )}
          {isError && (
            <div className="text-destructive px-6 py-10 text-center text-sm">
              Failed to load results.
            </div>
          )}
          {!isLoading && !isError && rows.length === 0 && (
            <div className="text-muted-foreground px-6 py-10 text-center text-sm">
              No universe scan results yet.{" "}
              <span className="block mt-1">
                Trigger the{" "}
                <Link to="/jobs" className="text-primary underline underline-offset-2">
                  universe_scan
                </Link>{" "}
                job to run a scan, or wait for the nightly schedule.
              </span>
            </div>
          )}
          {rows.length > 0 && (
            <>
              <ul className="divide-border/50 mx-3 divide-y md:hidden">
                {rows.map((row) => (
                  <DiscoveryMobileCard
                    key={row.symbol}
                    row={row}
                    alreadyWatchlist={watchlistSymbols.has(row.symbol)}
                  />
                ))}
              </ul>
              <div className="hidden overflow-x-auto md:block">
                <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Symbol</TableHead>
                    <TableHead>Sector</TableHead>
                    <TableHead className="text-right">Score</TableHead>
                    <TableHead className="text-right">Ann. Return</TableHead>
                    <TableHead className="text-right">Strike</TableHead>
                    <TableHead className="text-right">Expiration</TableHead>
                    <TableHead className="text-right">Premium</TableHead>
                    <TableHead className="text-right">Delta</TableHead>
                    <TableHead className="text-right">IV %ile</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.symbol}>
                      <TableCell className="font-mono font-semibold">
                        <Link
                          to={`/tickers/${row.symbol}`}
                          className="text-primary hover:underline"
                        >
                          {row.symbol}
                        </Link>
                      </TableCell>
                      <TableCell>
                        <SectorPill sector={row.sector} />
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm">
                        {row.score != null ? formatNumber(row.score, 1) : "—"}
                      </TableCell>
                      <TableCell
                        className={cn("text-right font-mono text-sm", annualizedColor(row.annualized_return))}
                      >
                        {row.annualized_return != null
                          ? formatPercent(row.annualized_return)
                          : "—"}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm">
                        {row.target_strike != null
                          ? `$${formatNumber(row.target_strike, 2)}`
                          : "—"}
                      </TableCell>
                      <TableCell className="text-right text-sm">
                        {row.target_expiration ? formatDate(row.target_expiration) : "—"}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm">
                        {row.target_premium != null
                          ? `$${formatNumber(row.target_premium, 2)}`
                          : "—"}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-right font-mono text-sm">
                        {row.target_delta != null
                          ? formatNumber(row.target_delta, 2)
                          : "—"}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm">
                        {row.iv_percentile != null
                          ? formatNumber(row.iv_percentile, 1)
                          : "—"}
                      </TableCell>
                      <TableCell className="text-right">
                        <AddButton
                          symbol={row.symbol}
                          alreadyWatchlist={watchlistSymbols.has(row.symbol)}
                        />
                      </TableCell>
                    </TableRow>
                  ))}
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

interface DiscoveryMobileCardProps {
  row: ScreenerResultRow;
  alreadyWatchlist: boolean;
}

function DiscoveryMobileCard({
  row,
  alreadyWatchlist,
}: DiscoveryMobileCardProps): JSX.Element {
  return (
    <li className="px-1 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Link
              to={`/tickers/${row.symbol}`}
              className="text-primary font-mono text-base font-semibold hover:underline"
            >
              {row.symbol}
            </Link>
            <SectorPill sector={row.sector} />
          </div>
          <div className="text-muted-foreground mt-0.5 font-mono text-xs">
            {row.target_strike != null ? `K $${formatNumber(row.target_strike, 2)}` : "—"}
            {row.target_expiration && ` · exp ${formatDate(row.target_expiration)}`}
            {row.target_premium != null && ` · prem $${formatNumber(row.target_premium, 2)}`}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div
            className={cn("font-mono text-base", annualizedColor(row.annualized_return))}
          >
            {row.annualized_return != null ? formatPercent(row.annualized_return) : "—"}
          </div>
          <div className="text-muted-foreground text-[10px] uppercase tracking-wider">
            ann. return
          </div>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2">
        <div className="bg-muted/30 rounded-md px-2 py-1.5">
          <div className="text-muted-foreground text-[10px] font-medium uppercase tracking-wider">
            Score
          </div>
          <div className="font-mono text-sm">
            {row.score != null ? formatNumber(row.score, 1) : "—"}
          </div>
        </div>
        <div className="bg-muted/30 rounded-md px-2 py-1.5">
          <div className="text-muted-foreground text-[10px] font-medium uppercase tracking-wider">
            Delta
          </div>
          <div className="font-mono text-sm">
            {row.target_delta != null ? formatNumber(row.target_delta, 2) : "—"}
          </div>
        </div>
        <div className="bg-muted/30 rounded-md px-2 py-1.5">
          <div className="text-muted-foreground text-[10px] font-medium uppercase tracking-wider">
            IV %ile
          </div>
          <div className="font-mono text-sm">
            {row.iv_percentile != null ? formatNumber(row.iv_percentile, 1) : "—"}
          </div>
        </div>
      </div>
      <div className="mt-2 flex justify-end">
        <AddButton symbol={row.symbol} alreadyWatchlist={alreadyWatchlist} />
      </div>
    </li>
  );
}
