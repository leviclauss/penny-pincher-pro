import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ChevronDown, Loader2, Plus, Settings, X } from "lucide-react";
import {
  createPortfolio,
  deletePortfolio,
  fetchPortfolios,
  fetchPositions,
} from "@/api/client";
import type {
  PortfolioOut,
  PositionLegOut,
  PositionOut,
  PositionState,
} from "@/api/types";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/Table";
import { OpenCoveredCallDialog } from "@/components/positions/OpenCoveredCallDialog";
import { OpenLongSharesDialog } from "@/components/positions/OpenLongSharesDialog";
import { OpenShortPutDialog } from "@/components/positions/OpenShortPutDialog";
import { cn } from "@/lib/utils";
import { formatDate, formatNumber } from "@/lib/format";
import { STATE_LABELS, STATE_TONES, formatCurrency, pnlTone } from "@/lib/positions";

type OpenDialog = null | "short_put" | "long_shares" | "covered_call";

type StateFilterValue = "all" | "open" | PositionState;
/** Portfolio filter: "all" = no filter, 0 = unassigned, positive int = portfolio id. */
type PortfolioFilterValue = "all" | number;

const STATE_FILTERS: { value: StateFilterValue; label: string }[] = [
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

function PortfolioBadge({ name }: { name: string | null }): JSX.Element {
  return (
    <span
      className={cn(
        "ring-border bg-muted/40 text-muted-foreground inline-flex max-w-[10rem] truncate rounded-full px-2 py-0.5 text-[10px] font-medium ring-1",
        name === null && "italic",
      )}
      title={name ?? "No portfolio"}
    >
      {name ?? "Unassigned"}
    </span>
  );
}

export function Positions(): JSX.Element {
  const navigate = useNavigate();
  const [stateFilter, setStateFilter] = useState<StateFilterValue>("open");
  const [portfolioFilter, setPortfolioFilter] = useState<PortfolioFilterValue>("all");
  const [openDialog, setOpenDialog] = useState<OpenDialog>(null);
  const [manageOpen, setManageOpen] = useState(false);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["positions", "list"],
    queryFn: () => fetchPositions(),
  });

  const { data: portfolios } = useQuery({
    queryKey: ["portfolios"],
    queryFn: fetchPortfolios,
  });

  const portfolioById = useMemo(() => {
    const m = new Map<number, PortfolioOut>();
    for (const p of portfolios ?? []) m.set(p.id, p);
    return m;
  }, [portfolios]);

  const filtered = useMemo(() => {
    if (!data) return [];
    let rows = data;
    if (stateFilter === "open") {
      rows = rows.filter((p) => p.state !== "closed");
    } else if (stateFilter !== "all") {
      rows = rows.filter((p) => p.state === stateFilter);
    }
    if (portfolioFilter === 0) {
      rows = rows.filter((p) => p.portfolio_id === null);
    } else if (portfolioFilter !== "all") {
      rows = rows.filter((p) => p.portfolio_id === portfolioFilter);
    }
    return rows;
  }, [data, stateFilter, portfolioFilter]);

  const stats = useMemo(() => {
    const scope = data ?? [];
    const inPortfolio =
      portfolioFilter === "all"
        ? scope
        : portfolioFilter === 0
          ? scope.filter((p) => p.portfolio_id === null)
          : scope.filter((p) => p.portfolio_id === portfolioFilter);
    const byState: Record<string, number> = {};
    let premium = 0;
    let open = 0;
    for (const p of inPortfolio) {
      byState[p.state] = (byState[p.state] ?? 0) + 1;
      if (p.state !== "closed") {
        open += 1;
        premium += totalPremium(p.legs);
      }
    }
    return { open, byState, premium };
  }, [data, portfolioFilter]);

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
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={() => setManageOpen(true)}
            title="Manage portfolios"
          >
            <Settings className="mr-1 h-4 w-4" />
            Portfolios
          </Button>
          <AddPositionMenu onChoose={setOpenDialog} />
        </div>
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
        <CardHeader className="space-y-3 px-3 sm:px-5">
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
            <CardTitle>
              {stateFilter === "all"
                ? "All positions"
                : stateFilter === "open"
                  ? "Open positions"
                  : STATE_LABELS[stateFilter]}
            </CardTitle>
            <div className="-mx-1 flex w-full snap-x gap-1 overflow-x-auto px-1 sm:mx-0 sm:w-auto sm:flex-wrap sm:overflow-visible sm:px-0">
              {STATE_FILTERS.map((f) => (
                <button
                  key={f.value}
                  type="button"
                  onClick={() => setStateFilter(f.value)}
                  className={cn(
                    "h-7 shrink-0 snap-start rounded-md border px-2.5 text-xs transition-colors",
                    stateFilter === f.value
                      ? "border-primary/40 bg-primary/15 text-primary-foreground"
                      : "border-border bg-background text-muted-foreground hover:text-foreground",
                  )}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>
          <PortfolioFilterRow
            portfolios={portfolios ?? []}
            value={portfolioFilter}
            onChange={setPortfolioFilter}
            onManage={() => setManageOpen(true)}
          />
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
            <>
              <ul className="divide-border/50 mx-3 divide-y md:hidden">
                {filtered.map((p) => (
                  <PositionMobileCard
                    key={p.id}
                    position={p}
                    portfolioName={
                      p.portfolio_id !== null
                        ? portfolioById.get(p.portfolio_id)?.name ?? null
                        : null
                    }
                    onOpen={() => navigate(`/positions/${p.id}`)}
                  />
                ))}
              </ul>
              <div className="hidden md:block">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Symbol</TableHead>
                      <TableHead>State</TableHead>
                      <TableHead>Portfolio</TableHead>
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
                      const portfolioName =
                        p.portfolio_id !== null
                          ? portfolioById.get(p.portfolio_id)?.name ?? null
                          : null;
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
                          <TableCell>
                            <PortfolioBadge name={portfolioName} />
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
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <OpenShortPutDialog
        open={openDialog === "short_put"}
        onOpenChange={(o) => !o && setOpenDialog(null)}
      />
      <OpenLongSharesDialog
        open={openDialog === "long_shares"}
        onOpenChange={(o) => !o && setOpenDialog(null)}
      />
      <OpenCoveredCallDialog
        open={openDialog === "covered_call"}
        onOpenChange={(o) => !o && setOpenDialog(null)}
      />
      <ManagePortfoliosDialog
        open={manageOpen}
        onOpenChange={setManageOpen}
        portfolios={portfolios ?? []}
        onPortfolioRemoved={(id) => {
          if (portfolioFilter === id) setPortfolioFilter("all");
        }}
      />
    </div>
  );
}

interface PortfolioFilterRowProps {
  portfolios: PortfolioOut[];
  value: PortfolioFilterValue;
  onChange: (v: PortfolioFilterValue) => void;
  onManage: () => void;
}

function PortfolioFilterRow({
  portfolios,
  value,
  onChange,
  onManage,
}: PortfolioFilterRowProps): JSX.Element {
  return (
    <div className="-mx-1 flex w-full snap-x items-center gap-1 overflow-x-auto px-1 sm:mx-0 sm:overflow-visible sm:px-0">
      <span className="text-muted-foreground shrink-0 text-[10px] font-semibold uppercase tracking-wider">
        Portfolio
      </span>
      <FilterChip selected={value === "all"} onClick={() => onChange("all")}>
        All
      </FilterChip>
      {portfolios.map((p) => (
        <FilterChip
          key={p.id}
          selected={value === p.id}
          onClick={() => onChange(p.id)}
        >
          {p.name}
        </FilterChip>
      ))}
      <FilterChip selected={value === 0} onClick={() => onChange(0)}>
        Unassigned
      </FilterChip>
      <button
        type="button"
        onClick={onManage}
        className="text-muted-foreground hover:text-foreground ml-auto h-7 shrink-0 snap-start rounded-md border border-dashed border-border px-2.5 text-xs transition-colors"
        title="Manage portfolios"
      >
        <Settings className="mr-1 inline h-3 w-3" />
        Manage
      </button>
    </div>
  );
}

function FilterChip({
  selected,
  onClick,
  children,
}: {
  selected: boolean;
  onClick: () => void;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "h-7 shrink-0 snap-start rounded-md border px-2.5 text-xs transition-colors",
        selected
          ? "border-primary/40 bg-primary/15 text-primary-foreground"
          : "border-border bg-background text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

interface PositionMobileCardProps {
  position: PositionOut;
  portfolioName: string | null;
  onOpen: () => void;
}

function PositionMobileCard({
  position,
  portfolioName,
  onOpen,
}: PositionMobileCardProps): JSX.Element {
  const leg = activeLeg(position);
  const snap = position.latest_snapshot;
  const legSummary = leg
    ? leg.leg_type === "shares"
      ? `${leg.shares ?? 0} sh @ ${formatNumber(leg.entry_price)}`
      : `${leg.contracts ?? 0}× ${leg.leg_type === "short_put" ? "put" : "call"} @ ${
          leg.strike !== null ? formatNumber(leg.strike) : "—"
        }`
    : "—";
  const mark =
    snap?.option_mid !== null && snap?.option_mid !== undefined
      ? formatNumber(snap.option_mid)
      : snap?.underlying_price !== null && snap?.underlying_price !== undefined
        ? formatNumber(snap.underlying_price)
        : "—";
  const pnlValue =
    snap?.unrealized_pnl !== null && snap?.unrealized_pnl !== undefined
      ? formatCurrency(snap.unrealized_pnl)
      : "—";
  const pctMax =
    snap?.pct_max_profit !== null && snap?.pct_max_profit !== undefined
      ? `${(snap.pct_max_profit * 100).toFixed(0)}%`
      : "—";

  return (
    <li
      onClick={onOpen}
      className="active:bg-accent/40 cursor-pointer px-1 py-3 transition-colors"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-base font-semibold tracking-tight">
              {position.symbol}
            </span>
            <StateBadge state={position.state} />
            <PortfolioBadge name={portfolioName} />
          </div>
          <div className="text-muted-foreground mt-0.5 truncate text-xs">
            {legSummary}
            {leg?.expiration && (
              <>
                {" · "}exp {formatDate(leg.expiration)}
                {snap?.dte !== null && snap?.dte !== undefined && ` (${snap.dte}d)`}
              </>
            )}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className={cn("font-mono text-base", pnlTone(snap?.unrealized_pnl ?? null))}>
            {pnlValue}
          </div>
          <div className="text-muted-foreground text-[10px] uppercase tracking-wider">
            unrealized
          </div>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2">
        <div className="bg-muted/30 rounded-md px-2 py-1.5">
          <div className="text-muted-foreground text-[10px] font-medium uppercase tracking-wider">
            Mark
          </div>
          <div className="font-mono text-sm">{mark}</div>
        </div>
        <div className="bg-muted/30 rounded-md px-2 py-1.5">
          <div className="text-muted-foreground text-[10px] font-medium uppercase tracking-wider">
            % Max
          </div>
          <div className="font-mono text-sm">{pctMax}</div>
        </div>
        <div className="bg-muted/30 rounded-md px-2 py-1.5">
          <div className="text-muted-foreground text-[10px] font-medium uppercase tracking-wider">
            Opened
          </div>
          <div className="font-mono text-sm">{formatDate(position.opened_at)}</div>
        </div>
      </div>
    </li>
  );
}

interface AddPositionMenuProps {
  onChoose: (type: Exclude<OpenDialog, null>) => void;
}

const ADD_POSITION_OPTIONS: {
  type: Exclude<OpenDialog, null>;
  label: string;
  hint: string;
}[] = [
  { type: "short_put", label: "Short put", hint: "Sell a cash-secured put" },
  {
    type: "long_shares",
    label: "Long shares",
    hint: "Track shares you already own",
  },
  {
    type: "covered_call",
    label: "Covered call",
    hint: "Shares you own with a call written",
  },
];

function AddPositionMenu({ onChoose }: AddPositionMenuProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent): void => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <Button onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open}>
        <Plus className="mr-1 h-4 w-4" />
        Add position
        <ChevronDown className="ml-1 h-4 w-4 opacity-70" />
      </Button>
      {open && (
        <div
          role="menu"
          className="border-border bg-popover absolute right-0 z-20 mt-1 w-64 overflow-hidden rounded-md border shadow-lg"
        >
          {ADD_POSITION_OPTIONS.map((opt) => (
            <button
              key={opt.type}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onChoose(opt.type);
              }}
              className="hover:bg-accent flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left transition-colors"
            >
              <span className="text-foreground text-sm font-medium">
                {opt.label}
              </span>
              <span className="text-muted-foreground text-xs">{opt.hint}</span>
            </button>
          ))}
        </div>
      )}
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

interface ManagePortfoliosDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  portfolios: PortfolioOut[];
  onPortfolioRemoved: (id: number) => void;
}

function ManagePortfoliosDialog({
  open,
  onOpenChange,
  portfolios,
  onPortfolioRemoved,
}: ManagePortfoliosDialogProps): JSX.Element {
  const qc = useQueryClient();
  const [name, setName] = useState("");

  const invalidate = (): void => {
    void qc.invalidateQueries({ queryKey: ["portfolios"] });
    void qc.invalidateQueries({ queryKey: ["positions"] });
  };

  const createMutation = useMutation({
    mutationFn: (n: string) => createPortfolio(n),
    onSuccess: () => {
      setName("");
      invalidate();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deletePortfolio(id),
    onSuccess: (_, id) => {
      onPortfolioRemoved(id);
      invalidate();
    },
  });

  useEffect(() => {
    if (!open) {
      setName("");
      createMutation.reset();
      deleteMutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const submit = (e: React.FormEvent): void => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    createMutation.mutate(trimmed);
  };

  const errorMessage =
    createMutation.error?.message ?? deleteMutation.error?.message ?? null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Manage portfolios</DialogTitle>
          <DialogDescription>
            Portfolios are tags for grouping positions (e.g. "IRA", "Taxable").
            Deleting one leaves its positions in place but unassigned.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="flex gap-2">
          <Input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="New portfolio name"
            maxLength={64}
          />
          <Button type="submit" disabled={!name.trim() || createMutation.isPending}>
            {createMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
          </Button>
        </form>

        {portfolios.length === 0 ? (
          <p className="text-muted-foreground py-3 text-center text-sm">
            No portfolios yet.
          </p>
        ) : (
          <ul className="divide-border/60 divide-y">
            {portfolios.map((p) => (
              <li key={p.id} className="flex items-center justify-between py-2">
                <div className="min-w-0 flex-1">
                  <div className="text-foreground truncate text-sm font-medium">
                    {p.name}
                  </div>
                  <div className="text-muted-foreground text-xs">
                    {p.position_count} position{p.position_count === 1 ? "" : "s"}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => deleteMutation.mutate(p.id)}
                  disabled={deleteMutation.isPending}
                  className="text-muted-foreground hover:text-destructive p-1 transition-colors"
                  title={`Delete ${p.name}`}
                >
                  <X className="h-4 w-4" />
                </button>
              </li>
            ))}
          </ul>
        )}

        {errorMessage && (
          <div className="text-destructive text-xs">{errorMessage}</div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
