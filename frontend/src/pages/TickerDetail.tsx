import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, TrendingDown, TrendingUp } from "lucide-react";
import {
  fetchTickerChart,
  fetchTickerIvHistory,
  fetchTickers,
  fetchUpcomingEarnings,
} from "@/api/client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { PriceChart } from "@/components/charts/PriceChart";
import { RsiChart } from "@/components/charts/RsiChart";
import { IvHistoryChart } from "@/components/charts/IvHistoryChart";
import { cn } from "@/lib/utils";
import { formatDate, formatNumber, formatPercent } from "@/lib/format";

export function TickerDetail(): JSX.Element {
  const { symbol } = useParams<{ symbol: string }>();
  const sym = symbol?.toUpperCase() ?? "";

  const tickers = useQuery({
    queryKey: ["tickers", { includeHidden: true }],
    queryFn: () => fetchTickers(true),
  });
  const chart = useQuery({
    queryKey: ["ticker", sym, "chart", "1y"],
    queryFn: () => fetchTickerChart(sym, "1y"),
    enabled: sym.length > 0,
  });
  const iv = useQuery({
    queryKey: ["ticker", sym, "iv", "1y"],
    queryFn: () => fetchTickerIvHistory(sym, "1y"),
    enabled: sym.length > 0,
  });
  const earnings = useQuery({
    queryKey: ["earnings", "upcoming", 90],
    queryFn: () => fetchUpcomingEarnings(90),
  });

  const ticker = tickers.data?.find((t) => t.symbol === sym);

  const dayChangePct = useMemo(() => {
    if (!chart.data || chart.data.length < 2) return null;
    const last = chart.data[chart.data.length - 1];
    const prev = chart.data[chart.data.length - 2];
    if (!last || !prev || prev.close === 0) return null;
    return ((last.close - prev.close) / prev.close) * 100;
  }, [chart.data]);

  if (!sym) {
    return <div className="text-muted-foreground">No symbol provided.</div>;
  }

  const up = dayChangePct !== null && dayChangePct >= 0;

  return (
    <div className="space-y-8">
      <div>
        <Link
          to="/tickers"
          className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs uppercase tracking-widest transition-colors"
        >
          <ChevronLeft className="h-3.5 w-3.5" /> Tickers
        </Link>
        <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-baseline gap-x-3">
              <h1 className="text-4xl font-semibold tracking-tight">{sym}</h1>
              <span className="text-muted-foreground text-base">
                {ticker?.name ?? "—"}
              </span>
              {ticker?.tier !== null && ticker?.tier !== undefined && (
                <span className="border-border/60 bg-card/60 text-muted-foreground rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider">
                  Tier {ticker.tier}
                </span>
              )}
              {ticker?.sector && (
                <span className="text-muted-foreground text-xs">{ticker.sector}</span>
              )}
            </div>
            <div className="mt-3 flex flex-wrap items-baseline gap-x-4 gap-y-1">
              <span className="text-3xl font-semibold tracking-tight tabular-nums">
                {ticker?.last_close !== null && ticker?.last_close !== undefined
                  ? formatNumber(ticker.last_close)
                  : "—"}
              </span>
              {dayChangePct !== null && (
                <span
                  className={cn(
                    "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-sm font-medium",
                    up
                      ? "bg-emerald-500/15 text-emerald-300"
                      : "bg-red-500/15 text-red-300",
                  )}
                >
                  {up ? (
                    <TrendingUp className="h-3.5 w-3.5" />
                  ) : (
                    <TrendingDown className="h-3.5 w-3.5" />
                  )}
                  {dayChangePct >= 0 ? "+" : ""}
                  {formatPercent(dayChangePct)}
                </span>
              )}
              {ticker?.last_close_date && (
                <span className="text-muted-foreground font-mono text-xs">
                  as of {formatDate(ticker.last_close_date)}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Price · 1 year</CardTitle>
            <span className="text-muted-foreground text-[11px]">
              Click EMA chips to toggle overlays
            </span>
          </div>
        </CardHeader>
        <CardContent>
          {chart.isLoading && (
            <div className="text-muted-foreground text-sm">Loading…</div>
          )}
          {chart.isError && (
            <div className="text-destructive text-sm">Failed to load chart.</div>
          )}
          {chart.data && chart.data.length === 0 && (
            <div className="text-muted-foreground text-sm">
              No bars yet for this symbol.
            </div>
          )}
          {chart.data && chart.data.length > 0 && (
            <>
              <PriceChart
                bars={chart.data}
                earnings={earnings.data?.filter((e) => e.symbol === sym) ?? []}
                syncId={`ticker-${sym}`}
              />
              <div className="mt-6">
                <h3 className="text-muted-foreground mb-2 text-[10px] font-semibold uppercase tracking-widest">
                  RSI (14)
                </h3>
                <RsiChart bars={chart.data} syncId={`ticker-${sym}`} />
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Implied volatility · 1 year</CardTitle>
          <p className="text-muted-foreground text-xs">
            ATM IV for the front-month option chain.
          </p>
        </CardHeader>
        <CardContent>
          {iv.isLoading && <div className="text-muted-foreground text-sm">Loading…</div>}
          {iv.isError && (
            <div className="text-destructive text-sm">Failed to load IV history.</div>
          )}
          {iv.data && <IvHistoryChart points={iv.data} />}
        </CardContent>
      </Card>
    </div>
  );
}
