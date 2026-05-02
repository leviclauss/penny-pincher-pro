import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import { fetchPositions } from "@/api/client";
import type { PositionLegOut, PositionOut, PositionState } from "@/api/types";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/Table";
import { OpenShortPutDialog } from "@/components/positions/OpenShortPutDialog";
import { cn } from "@/lib/utils";
import { formatDate, formatNumber } from "@/lib/format";
import { STATE_LABELS, STATE_TONES, formatCurrency, pnlTone } from "@/lib/positions";

type FilterValue = "all" | "open" | PositionState;

const FILTERS: { value: FilterValue; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "short_put", label: "Short put" },
  { value: "long_shares", label: "Long shares" },
  { value: "covered_call", label: "Covered call" },
  { value: "closed", label: "Closed" },
  { value: "all", label: "All" },
];

function activeLeg(position: PositionOut): PositionLegOut | null {
  if (position.state === "short_put") {
    return position.legs.find((l) => l.leg_type === "short_put" && l.outcome === "open") ?? null;
  }
  if (position.state === "covered_call") {
    return (
      position.legs.find((l) => l.leg_type === "covered_call" && l.outcome === "open") ?? null
    );
  }
  if (position.state === "long_shares") {
    return position.legs.find((l) => l.leg_type === "shares" && l.outcome === "open") ?? null;
  }
  return null;
}

function totalPremium(legs: PositionLegOut[]): number {
  let total = 0;
  for (const leg of legs) {
    if (
      (leg.leg_type === "short_put" || leg.leg_type === "covered_call") &&
      leg.entry_price !== null &&
      leg.contracts !== null
    ) {
      total += leg.entry_price * leg.contracts * 100;
    }
  }
  return total;
}

function StateBadge({ state }: { state: string }): JSX.Element {
  return (
    <span
      className={cn(
        "inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ring-1",
        STATE_TONES[state] ?? STATE_TONES.closed,
      )}
    >
      {STATE_LABELS[state] ?? state}
    </span>
  );
}

export function Positions(): JSX.Element {
  const navigate = useNavigate();
  const [filter, setFilter] = useState<FilterValue>("open");
  const [openDialog, setOpenDialog] = useState(false);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["positions", "list"],
    queryFn: () => fetchPositions(),
  });

  const filtered = useMemo(() => {
    if (!data) return [];
    if (filter === "all") return data;
    if (filter === "open") return data.filter((p) => p.state !== "closed");
    return data.filter((p) => p.state === filter);
  }, [data, filter]);

  const stats = useMemo(() => {
    if (!data) return { open: 0, byState: {} as Record<string, number>, premium: 0 };
    const byState: Record<string, number> = {};
    let premium = 0;
    let open = 0;
    for (const p of data) {
      byState[p.state] = (byState[p.state] ?? 0) + 1;
      if (p.state !== "closed") {
        open += 1;
        premium += totalPremium(p.legs);
      }
    }
    return { open, byState, premium };
  }, [data]);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1.5">
          <p className="text-primary text-xs font-semibold uppercase tracking-widest">
            Portfolio
          </p>
          <h1 className="text-3xl font-semibold tracking-tight">Positions</h1>
          <p className="text-muted-foreground max-w-2xl text-sm">
            Manually-tracked wheel cycles. Mark-to-market values come from the
            most recent <code className="font-mono">position_management</code>{" "}
            run; trigger it from the Jobs page if numbers look stale.
          </p>
        </div>
        <Button onClick={() => setOpenDialog(true)}>
          <Plus className="mr-1 h-4 w-4" />
          Open short put
        </Button>
      </header>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Open positions" value={String(stats.open)} />
        <StatCard
          label="Short puts"
          value={String(stats.byState.short_put ?? 0)}
        />
        <StatCard
          label="Covered calls"
          value={String(stats.byState.covered_call ?? 0)}
        />
        <StatCard
          label="Premium collected"
          value={formatCurrency(stats.premium, 0)}
          tone="text-emerald-300"
        />
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <CardTitle>
              {filter === "all"
                ? "All positions"
                : filter === "open"
                  ? "Open positions"
                  : STATE_LABELS[filter]}
            </CardTitle>
            <div className="flex flex-wrap items-center gap-1">
              {FILTERS.map((f) => (
                <button
                  key={f.value}
                  type="button"
                  onClick={() => setFilter(f.value)}
                  className={cn(
                    "h-7 rounded-md border px-2.5 text-xs transition-colors",
                    filter === f.value
                      ? "border-primary/40 bg-primary/15 text-primary-foreground"
                      : "border-border bg-background text-muted-foreground hover:text-foreground",
                  )}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>
        </CardHeader>
        <CardContent className="px-0">
          {isLoading ? (
            <div className="text-muted-foreground px-5 py-8 text-center text-sm">
              Loading…
            </div>
          ) : isError ? (
            <div className="text-destructive px-5 py-8 text-center text-sm">
              Failed to load positions.
            </div>
          ) : filtered.length === 0 ? (
            <div className="text-muted-foreground px-5 py-8 text-center text-sm">
              {data && data.length === 0
                ? "No positions yet. Click Open short put to start a cycle."
                : "No positions match this filter."}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Symbol</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead>Leg</TableHead>
                  <TableHead className="text-right">Strike</TableHead>
                  <TableHead className="text-right">Exp / DTE</TableHead>
                  <TableHead className="text-right">Mark</TableHead>
                  <TableHead className="text-right">Unrealized P&amp;L</TableHead>
                  <TableHead className="text-right">% Max</TableHead>
                  <TableHead className="text-right">Opened</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((p) => {
                  const leg = activeLeg(p);
                  const snap = p.latest_snapshot;
                  return (
                    <TableRow
                      key={p.id}
                      onClick={() => navigate(`/positions/${p.id}`)}
                      className="cursor-pointer"
                    >
                      <TableCell className="font-semibold tracking-tight">
                        {p.symbol}
                      </TableCell>
                      <TableCell>
                        <StateBadge state={p.state} />
                      </TableCell>
                      <TableCell className="text-muted-foreground text-xs">
                        {leg
                          ? leg.leg_type === "shares"
                            ? `${leg.shares ?? 0} shares @ ${formatNumber(leg.entry_price)}`
                            : `${leg.contracts ?? 0}× ${leg.leg_type === "short_put" ? "put" : "call"}`
                          : "—"}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {leg && leg.strike !== null ? formatNumber(leg.strike) : "—"}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-right font-mono text-xs">
                        {leg?.expiration ? (
                          <>
                            {formatDate(leg.expiration)}
                            {snap?.dte !== null && snap?.dte !== undefined && (
                              <span className="ml-1 opacity-70">({snap.dte}d)</span>
                            )}
                          </>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {snap?.option_mid !== null && snap?.option_mid !== undefined
                          ? formatNumber(snap.option_mid)
                          : snap?.underlying_price !== null && snap?.underlying_price !== undefined
                            ? formatNumber(snap.underlying_price)
                            : "—"}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right font-mono",
                          pnlTone(snap?.unrealized_pnl ?? null),
                        )}
                      >
                        {snap?.unrealized_pnl !== null && snap?.unrealized_pnl !== undefined
                          ? formatCurrency(snap.unrealized_pnl)
                          : "—"}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {snap?.pct_max_profit !== null && snap?.pct_max_profit !== undefined
                          ? `${(snap.pct_max_profit * 100).toFixed(0)}%`
                          : "—"}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-right font-mono text-xs">
                        {formatDate(p.opened_at)}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <OpenShortPutDialog open={openDialog} onOpenChange={setOpenDialog} />
    </div>
  );
}

interface StatCardProps {
  label: string;
  value: string;
  tone?: string;
}

function StatCard({ label, value, tone }: StatCardProps): JSX.Element {
  return (
    <Card>
      <CardContent className="px-5 py-4">
        <div className="text-muted-foreground text-[10px] font-semibold uppercase tracking-widest">
          {label}
        </div>
        <div
          className={cn(
            "mt-1 font-mono text-2xl font-semibold tracking-tight",
            tone,
          )}
        >
          {value}
        </div>
      </CardContent>
    </Card>
  );
}
