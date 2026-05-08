import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, FileText, Loader2, Pencil, Save, Trash2 } from "lucide-react";
import {
  fetchPosition,
  fetchPositionAttribution,
  patchPosition,
} from "@/api/client";
import type { PositionLegOut, PositionOut, PositionSnapshotOut } from "@/api/types";
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
import { DeletePositionDialog } from "@/components/positions/DeletePositionDialog";
import { EditClosedPositionDialog } from "@/components/positions/EditClosedPositionDialog";
import {
  TransitionDialog,
  type TransitionKind,
} from "@/components/positions/TransitionDialog";
import { cn } from "@/lib/utils";
import { formatDate, formatDateTime, formatNumber } from "@/lib/format";
import { STATE_LABELS, STATE_TONES, formatCurrency, pnlTone } from "@/lib/positions";

const LEG_LABELS: Record<string, string> = {
  short_put: "Short put",
  covered_call: "Covered call",
  shares: "Shares",
};

const OUTCOME_TONES: Record<string, string> = {
  open: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
  closed: "bg-muted text-muted-foreground ring-border",
  expired: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  assigned: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  called_away: "bg-violet-500/15 text-violet-300 ring-violet-500/30",
};

export function PositionDetail(): JSX.Element {
  const { id } = useParams<{ id: string }>();
  const positionId = id ? Number(id) : NaN;

  const positionQuery = useQuery({
    queryKey: ["position", positionId],
    queryFn: () => fetchPosition(positionId),
    enabled: Number.isFinite(positionId),
  });
  const attributionQuery = useQuery({
    queryKey: ["position", positionId, "attribution"],
    queryFn: () => fetchPositionAttribution(positionId),
    enabled: Number.isFinite(positionId) && positionQuery.data?.state === "closed",
  });

  const [transition, setTransition] = useState<TransitionKind | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  if (!Number.isFinite(positionId)) {
    return <div className="text-muted-foreground">Invalid position id.</div>;
  }
  if (positionQuery.isLoading) {
    return <div className="text-muted-foreground">Loading…</div>;
  }
  if (positionQuery.isError || !positionQuery.data) {
    return <div className="text-destructive">Failed to load position.</div>;
  }

  const position = positionQuery.data;

  return (
    <div className="space-y-6">
      <div>
        <Link
          to="/positions"
          className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs uppercase tracking-widest transition-colors"
        >
          <ChevronLeft className="h-3.5 w-3.5" /> Positions
        </Link>
        <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-baseline gap-x-3">
              <h1 className="text-3xl font-semibold tracking-tight">
                {position.symbol}
              </h1>
              <span
                className={cn(
                  "inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ring-1",
                  STATE_TONES[position.state] ?? STATE_TONES.closed,
                )}
              >
                {STATE_LABELS[position.state] ?? position.state}
              </span>
              <span className="text-muted-foreground text-xs">
                Cycle #{position.cycle_id ?? position.id}
              </span>
            </div>
            <div className="text-muted-foreground mt-1 font-mono text-xs">
              Opened {formatDateTime(position.opened_at)}
              {position.closed_at && (
                <>
                  {" "}
                  · Closed {formatDateTime(position.closed_at)}
                </>
              )}
            </div>
          </div>
          <ActionBar
            state={position.state}
            onTrigger={(kind) => setTransition(kind)}
            onEdit={() => setEditOpen(true)}
            onDelete={() => setDeleteOpen(true)}
          />
        </div>
      </div>

      <SnapshotCard snapshot={position.latest_snapshot} state={position.state} />

      {position.state === "closed" && (
        <AttributionCard
          loading={attributionQuery.isLoading}
          attribution={attributionQuery.data ?? null}
        />
      )}

      <Card>
        <CardHeader>
          <CardTitle>Legs</CardTitle>
        </CardHeader>
        <CardContent className="px-0">
          <LegsTable legs={position.legs} />
        </CardContent>
      </Card>

      <NotesCard position={position} />

      <TransitionDialog
        position={position}
        kind={transition}
        onClose={() => setTransition(null)}
      />
      {position.state === "closed" && (
        <>
          <EditClosedPositionDialog
            position={position}
            open={editOpen}
            onOpenChange={setEditOpen}
          />
          <DeletePositionDialog
            position={position}
            open={deleteOpen}
            onOpenChange={setDeleteOpen}
          />
        </>
      )}
    </div>
  );
}

interface ActionBarProps {
  state: string;
  onTrigger: (kind: TransitionKind) => void;
  onEdit: () => void;
  onDelete: () => void;
}

function ActionBar({
  state,
  onTrigger,
  onEdit,
  onDelete,
}: ActionBarProps): JSX.Element | null {
  if (state === "short_put") {
    return (
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => onTrigger("close_put")}>Close put</Button>
        <Button variant="outline" onClick={() => onTrigger("expire_put")}>
          Expire
        </Button>
        <Button variant="outline" onClick={() => onTrigger("assign_put")}>
          Assign
        </Button>
      </div>
    );
  }
  if (state === "long_shares") {
    return (
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => onTrigger("open_call")}>Sell covered call</Button>
        <Button variant="outline" onClick={() => onTrigger("close_shares")}>
          Sell shares
        </Button>
      </div>
    );
  }
  if (state === "covered_call") {
    return (
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => onTrigger("close_call")}>Close call</Button>
        <Button variant="outline" onClick={() => onTrigger("expire_call")}>
          Expire
        </Button>
        <Button variant="outline" onClick={() => onTrigger("called_away")}>
          Called away
        </Button>
      </div>
    );
  }
  if (state === "closed") {
    return (
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={onEdit}>
          <Pencil className="mr-1 h-3.5 w-3.5" />
          Edit
        </Button>
        <Button variant="destructive" onClick={onDelete}>
          <Trash2 className="mr-1 h-3.5 w-3.5" />
          Delete
        </Button>
      </div>
    );
  }
  return null;
}

interface SnapshotCardProps {
  snapshot: PositionSnapshotOut | null;
  state: string;
}

function SnapshotCard({ snapshot, state }: SnapshotCardProps): JSX.Element {
  if (state === "closed") {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Latest mark</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-muted-foreground text-sm">
            Position is closed — see Attribution below for the cycle's realized
            metrics.
          </div>
        </CardContent>
      </Card>
    );
  }
  if (snapshot === null) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Latest mark</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-muted-foreground text-sm">
            No snapshot yet. Trigger the{" "}
            <code className="font-mono">position_management</code> job from the
            Jobs page or wait for the next scheduled run.
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-baseline justify-between">
          <CardTitle>Latest mark</CardTitle>
          <span className="text-muted-foreground font-mono text-xs">
            {formatDateTime(snapshot.snapshot_at)}
          </span>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-6">
          <Metric label="Underlying" value={formatNumber(snapshot.underlying_price)} />
          <Metric label="Option mid" value={formatNumber(snapshot.option_mid)} />
          <Metric
            label="Unrealized P&L"
            value={formatCurrency(snapshot.unrealized_pnl)}
            tone={pnlTone(snapshot.unrealized_pnl)}
          />
          <Metric
            label="% Max profit"
            value={
              snapshot.pct_max_profit === null
                ? "—"
                : `${(snapshot.pct_max_profit * 100).toFixed(0)}%`
            }
          />
          <Metric label="Delta" value={formatNumber(snapshot.delta, 2)} />
          <Metric label="DTE" value={snapshot.dte === null ? "—" : `${snapshot.dte}d`} />
        </div>
      </CardContent>
    </Card>
  );
}

interface AttributionCardProps {
  loading: boolean;
  attribution: import("@/api/types").PositionAttributionOut | null;
}

function AttributionCard({ loading, attribution }: AttributionCardProps): JSX.Element {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Attribution</CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="text-muted-foreground text-sm">Loading…</div>
        ) : attribution === null ? (
          <div className="text-muted-foreground text-sm">No attribution available.</div>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
            <Metric label="Days in cycle" value={attribution.days_in_cycle === null ? "—" : `${attribution.days_in_cycle}d`} />
            <Metric
              label="Premium collected"
              value={formatCurrency(attribution.total_premium_collected)}
              tone="text-emerald-300"
            />
            <Metric
              label="Shares P&L"
              value={formatCurrency(attribution.shares_pnl)}
              tone={pnlTone(attribution.shares_pnl)}
            />
            <Metric
              label="Total realized"
              value={formatCurrency(attribution.realized_pnl)}
              tone={pnlTone(attribution.realized_pnl)}
            />
            <Metric
              label="Cost basis / share"
              value={formatNumber(attribution.cost_basis_per_share)}
            />
            <Metric
              label="Capital tied up"
              value={formatCurrency(attribution.capital_tied_up, 0)}
            />
            <Metric
              label="Annualized return"
              value={
                attribution.annualized_return === null
                  ? "—"
                  : `${(attribution.annualized_return * 100).toFixed(1)}%`
              }
              tone={pnlTone(attribution.annualized_return)}
            />
            <Metric
              label="Outcome"
              value={attribution.was_assigned ? "Assigned" : "Premium only"}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function LegsTable({ legs }: { legs: PositionLegOut[] }): JSX.Element {
  if (legs.length === 0) {
    return (
      <div className="text-muted-foreground px-5 py-8 text-center text-sm">
        No legs recorded.
      </div>
    );
  }
  return (
    <>
      <ul className="divide-border/50 mx-3 divide-y md:hidden">
        {legs.map((leg) => (
          <LegMobileCard key={leg.id} leg={leg} />
        ))}
      </ul>
      <div className="hidden overflow-x-auto md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Type</TableHead>
              <TableHead>Outcome</TableHead>
              <TableHead className="text-right">Qty</TableHead>
              <TableHead className="text-right">Strike</TableHead>
              <TableHead className="text-right">Expiration</TableHead>
              <TableHead className="text-right">Entry</TableHead>
              <TableHead className="text-right">Exit</TableHead>
              <TableHead className="text-right">Opened</TableHead>
              <TableHead className="text-right">Closed</TableHead>
              <TableHead className="text-right">Fees</TableHead>
              <TableHead className="text-right">Realized P&amp;L</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {legs.map((leg) => (
              <TableRow key={leg.id}>
                <TableCell className="font-medium">
                  {LEG_LABELS[leg.leg_type] ?? leg.leg_type}
                </TableCell>
                <TableCell>
                  {leg.outcome ? (
                    <span
                      className={cn(
                        "inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ring-1",
                        OUTCOME_TONES[leg.outcome] ?? OUTCOME_TONES.closed,
                      )}
                    >
                      {leg.outcome.replace("_", " ")}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="text-right font-mono">
                  {leg.shares !== null
                    ? `${leg.shares} sh`
                    : leg.contracts !== null
                      ? `${leg.contracts}×`
                      : "—"}
                </TableCell>
                <TableCell className="text-right font-mono">
                  {formatNumber(leg.strike)}
                </TableCell>
                <TableCell className="text-muted-foreground text-right font-mono text-xs">
                  {formatDate(leg.expiration)}
                </TableCell>
                <TableCell className="text-right font-mono">
                  {formatNumber(leg.entry_price)}
                </TableCell>
                <TableCell className="text-right font-mono">
                  {formatNumber(leg.exit_price)}
                </TableCell>
                <TableCell className="text-muted-foreground text-right font-mono text-xs">
                  {formatDate(leg.entry_date)}
                </TableCell>
                <TableCell className="text-muted-foreground text-right font-mono text-xs">
                  {formatDate(leg.exit_date)}
                </TableCell>
                <TableCell className="text-right font-mono">
                  {leg.fees === 0 ? "—" : formatCurrency(leg.fees)}
                </TableCell>
                <TableCell
                  className={cn("text-right font-mono", pnlTone(leg.realized_pnl))}
                >
                  {leg.realized_pnl === null ? "—" : formatCurrency(leg.realized_pnl)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </>
  );
}

function LegMobileCard({ leg }: { leg: PositionLegOut }): JSX.Element {
  const qty =
    leg.shares !== null
      ? `${leg.shares} sh`
      : leg.contracts !== null
        ? `${leg.contracts}×`
        : "—";
  return (
    <li className="px-1 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold tracking-tight">
              {LEG_LABELS[leg.leg_type] ?? leg.leg_type}
            </span>
            {leg.outcome ? (
              <span
                className={cn(
                  "inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ring-1",
                  OUTCOME_TONES[leg.outcome] ?? OUTCOME_TONES.closed,
                )}
              >
                {leg.outcome.replace("_", " ")}
              </span>
            ) : null}
          </div>
          <div className="text-muted-foreground mt-0.5 font-mono text-xs">
            {qty}
            {leg.strike !== null && ` · K ${formatNumber(leg.strike)}`}
            {leg.expiration && ` · exp ${formatDate(leg.expiration)}`}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className={cn("font-mono text-sm", pnlTone(leg.realized_pnl))}>
            {leg.realized_pnl === null ? "—" : formatCurrency(leg.realized_pnl)}
          </div>
          <div className="text-muted-foreground text-[10px] uppercase tracking-wider">
            realized
          </div>
        </div>
      </div>
      <div className="mt-2 grid grid-cols-3 gap-2">
        <div className="bg-muted/30 rounded-md px-2 py-1.5">
          <div className="text-muted-foreground text-[10px] font-medium uppercase tracking-wider">
            Entry
          </div>
          <div className="font-mono text-xs">
            {formatNumber(leg.entry_price)}
            {leg.entry_date && (
              <span className="text-muted-foreground ml-1 text-[10px]">
                {formatDate(leg.entry_date)}
              </span>
            )}
          </div>
        </div>
        <div className="bg-muted/30 rounded-md px-2 py-1.5">
          <div className="text-muted-foreground text-[10px] font-medium uppercase tracking-wider">
            Exit
          </div>
          <div className="font-mono text-xs">
            {formatNumber(leg.exit_price)}
            {leg.exit_date && (
              <span className="text-muted-foreground ml-1 text-[10px]">
                {formatDate(leg.exit_date)}
              </span>
            )}
          </div>
        </div>
        <div className="bg-muted/30 rounded-md px-2 py-1.5">
          <div className="text-muted-foreground text-[10px] font-medium uppercase tracking-wider">
            Fees
          </div>
          <div className="font-mono text-xs">
            {leg.fees === 0 ? "—" : formatCurrency(leg.fees)}
          </div>
        </div>
      </div>
    </li>
  );
}

function NotesCard({ position }: { position: PositionOut }): JSX.Element {
  const qc = useQueryClient();
  const [text, setText] = useState(position.notes ?? "");

  useEffect(() => {
    setText(position.notes ?? "");
  }, [position.notes]);

  const mutation = useMutation({
    mutationFn: (notes: string | null) => patchPosition(position.id, { notes }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["position", position.id] });
      void qc.invalidateQueries({ queryKey: ["positions"] });
    },
  });

  const dirty = (text || "") !== (position.notes || "");

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileText className="h-4 w-4 opacity-70" />
          Notes
        </CardTitle>
      </CardHeader>
      <CardContent>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          placeholder="Strategy thinking, why we entered, exit triggers…"
          className="border-border bg-background text-foreground placeholder:text-muted-foreground focus-visible:ring-ring w-full rounded-md border px-3 py-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2"
        />
        <div className="mt-2 flex items-center justify-end gap-2">
          {mutation.error && (
            <span className="text-destructive text-xs">{mutation.error.message}</span>
          )}
          <Button
            size="sm"
            onClick={() => mutation.mutate(text.trim() || null)}
            disabled={!dirty || mutation.isPending}
          >
            {mutation.isPending ? (
              <>
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                Saving…
              </>
            ) : (
              <>
                <Save className="mr-1 h-3 w-3" />
                Save notes
              </>
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

interface MetricProps {
  label: string;
  value: string;
  tone?: string;
}

function Metric({ label, value, tone }: MetricProps): JSX.Element {
  return (
    <div>
      <div className="text-muted-foreground text-[10px] font-semibold uppercase tracking-widest">
        {label}
      </div>
      <div className={cn("mt-1 font-mono text-base font-semibold tabular-nums", tone)}>
        {value}
      </div>
    </div>
  );
}
