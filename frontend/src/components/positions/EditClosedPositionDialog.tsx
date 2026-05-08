import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import {
  patchPosition,
  patchPositionLeg,
  type PositionLegPatch,
} from "@/api/client";
import type { PositionLegOut, PositionOut } from "@/api/types";
import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";

interface Props {
  position: PositionOut;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const LEG_LABELS: Record<string, string> = {
  short_put: "Short put",
  covered_call: "Covered call",
  shares: "Shares",
};

interface PositionForm {
  opened_at: string;
  closed_at: string;
}

interface LegForm {
  entry_price: string;
  exit_price: string;
  entry_date: string;
  exit_date: string;
  fees: string;
  realized_pnl: string;
}

function isoToDateInput(value: string | null): string {
  if (!value) return "";
  return value.slice(0, 10);
}

function dateInputToIsoUtc(value: string): string {
  // The backend stores opened_at/closed_at as timezone-aware datetimes.
  // We treat the picker value as a UTC date at midnight to keep the round-trip stable.
  if (!value) return "";
  return `${value}T00:00:00Z`;
}

function legToForm(leg: PositionLegOut): LegForm {
  return {
    entry_price: leg.entry_price === null ? "" : String(leg.entry_price),
    exit_price: leg.exit_price === null ? "" : String(leg.exit_price),
    entry_date: leg.entry_date ?? "",
    exit_date: leg.exit_date ?? "",
    fees: String(leg.fees ?? 0),
    realized_pnl: leg.realized_pnl === null ? "" : String(leg.realized_pnl),
  };
}

function diffLegPatch(leg: PositionLegOut, form: LegForm): PositionLegPatch | null {
  const patch: PositionLegPatch = {};
  let changed = false;

  const numOrNull = (s: string): number | null => (s === "" ? null : Number(s));
  const strOrNull = (s: string): string | null => (s === "" ? null : s);

  const ep = numOrNull(form.entry_price);
  if (ep !== leg.entry_price) {
    patch.entry_price = ep;
    changed = true;
  }
  const xp = numOrNull(form.exit_price);
  if (xp !== leg.exit_price) {
    patch.exit_price = xp;
    changed = true;
  }
  const ed = strOrNull(form.entry_date);
  if (ed !== leg.entry_date) {
    patch.entry_date = ed;
    changed = true;
  }
  const xd = strOrNull(form.exit_date);
  if (xd !== leg.exit_date) {
    patch.exit_date = xd;
    changed = true;
  }
  const fees = form.fees === "" ? 0 : Number(form.fees);
  if (fees !== leg.fees) {
    patch.fees = fees;
    changed = true;
  }
  const rpnl = numOrNull(form.realized_pnl);
  if (rpnl !== leg.realized_pnl) {
    patch.realized_pnl = rpnl;
    changed = true;
  }

  return changed ? patch : null;
}

export function EditClosedPositionDialog({
  position,
  open,
  onOpenChange,
}: Props): JSX.Element {
  const qc = useQueryClient();

  const initialPosition: PositionForm = useMemo(
    () => ({
      opened_at: isoToDateInput(position.opened_at),
      closed_at: isoToDateInput(position.closed_at),
    }),
    [position.opened_at, position.closed_at],
  );

  const [posForm, setPosForm] = useState<PositionForm>(initialPosition);
  const [legForms, setLegForms] = useState<Record<number, LegForm>>(() =>
    Object.fromEntries(position.legs.map((l) => [l.id, legToForm(l)])),
  );

  useEffect(() => {
    if (open) {
      setPosForm(initialPosition);
      setLegForms(
        Object.fromEntries(position.legs.map((l) => [l.id, legToForm(l)])),
      );
    }
  }, [open, initialPosition, position.legs]);

  const mutation = useMutation({
    mutationFn: async (): Promise<void> => {
      // Apply leg edits first so the position-level patch's response reflects everything.
      for (const leg of position.legs) {
        const form = legForms[leg.id];
        if (!form) continue;
        const patch = diffLegPatch(leg, form);
        if (patch !== null) {
          await patchPositionLeg(position.id, leg.id, patch);
        }
      }
      const positionPatch: {
        opened_at?: string;
        closed_at?: string;
      } = {};
      if (
        posForm.opened_at &&
        posForm.opened_at !== isoToDateInput(position.opened_at)
      ) {
        positionPatch.opened_at = dateInputToIsoUtc(posForm.opened_at);
      }
      if (
        posForm.closed_at &&
        posForm.closed_at !== isoToDateInput(position.closed_at)
      ) {
        positionPatch.closed_at = dateInputToIsoUtc(posForm.closed_at);
      }
      if (Object.keys(positionPatch).length > 0) {
        await patchPosition(position.id, positionPatch);
      }
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["positions"] });
      void qc.invalidateQueries({ queryKey: ["position", position.id] });
      onOpenChange(false);
    },
  });

  const updateLeg = (legId: number, key: keyof LegForm, value: string): void => {
    setLegForms((prev) => ({
      ...prev,
      [legId]: { ...prev[legId], [key]: value },
    }));
  };

  const submit = (e: React.FormEvent): void => {
    e.preventDefault();
    mutation.mutate();
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !mutation.isPending && onOpenChange(o)}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>Edit closed position · {position.symbol}</DialogTitle>
            <DialogDescription>
              Correct data-entry mistakes on a closed cycle. Editing legs
              changes recorded P&amp;L and attribution; new mark-to-market
              snapshots will not be regenerated.
            </DialogDescription>
          </DialogHeader>

          <section className="space-y-3">
            <h3 className="text-foreground text-sm font-semibold">Cycle dates</h3>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Opened on" htmlFor="ec-opened">
                <Input
                  id="ec-opened"
                  type="date"
                  value={posForm.opened_at}
                  onChange={(e) =>
                    setPosForm((p) => ({ ...p, opened_at: e.target.value }))
                  }
                  required
                />
              </Field>
              <Field label="Closed on" htmlFor="ec-closed">
                <Input
                  id="ec-closed"
                  type="date"
                  value={posForm.closed_at}
                  onChange={(e) =>
                    setPosForm((p) => ({ ...p, closed_at: e.target.value }))
                  }
                  required
                />
              </Field>
            </div>
          </section>

          <section className="mt-6 space-y-4">
            <h3 className="text-foreground text-sm font-semibold">Legs</h3>
            {position.legs.length === 0 ? (
              <p className="text-muted-foreground text-sm">No legs to edit.</p>
            ) : (
              position.legs.map((leg) => {
                const form = legForms[leg.id];
                if (!form) return null;
                return (
                  <div
                    key={leg.id}
                    className="border-border rounded-md border p-3"
                  >
                    <div className="text-foreground mb-2 flex items-baseline justify-between">
                      <span className="text-sm font-medium">
                        {LEG_LABELS[leg.leg_type] ?? leg.leg_type}
                      </span>
                      <span className="text-muted-foreground font-mono text-xs">
                        {leg.shares !== null
                          ? `${leg.shares} sh`
                          : leg.contracts !== null
                            ? `${leg.contracts}× @ K${leg.strike ?? "—"}`
                            : "—"}
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <Field label="Entry price" htmlFor={`ec-${leg.id}-ep`}>
                        <Input
                          id={`ec-${leg.id}-ep`}
                          type="number"
                          step="0.01"
                          value={form.entry_price}
                          onChange={(e) =>
                            updateLeg(leg.id, "entry_price", e.target.value)
                          }
                        />
                      </Field>
                      <Field label="Exit price" htmlFor={`ec-${leg.id}-xp`}>
                        <Input
                          id={`ec-${leg.id}-xp`}
                          type="number"
                          step="0.01"
                          value={form.exit_price}
                          onChange={(e) =>
                            updateLeg(leg.id, "exit_price", e.target.value)
                          }
                        />
                      </Field>
                      <Field label="Entry date" htmlFor={`ec-${leg.id}-ed`}>
                        <Input
                          id={`ec-${leg.id}-ed`}
                          type="date"
                          value={form.entry_date}
                          onChange={(e) =>
                            updateLeg(leg.id, "entry_date", e.target.value)
                          }
                        />
                      </Field>
                      <Field label="Exit date" htmlFor={`ec-${leg.id}-xd`}>
                        <Input
                          id={`ec-${leg.id}-xd`}
                          type="date"
                          value={form.exit_date}
                          onChange={(e) =>
                            updateLeg(leg.id, "exit_date", e.target.value)
                          }
                        />
                      </Field>
                      <Field label="Fees" htmlFor={`ec-${leg.id}-fees`}>
                        <Input
                          id={`ec-${leg.id}-fees`}
                          type="number"
                          step="0.01"
                          min="0"
                          value={form.fees}
                          onChange={(e) =>
                            updateLeg(leg.id, "fees", e.target.value)
                          }
                        />
                      </Field>
                      <Field label="Realized P&L" htmlFor={`ec-${leg.id}-rpnl`}>
                        <Input
                          id={`ec-${leg.id}-rpnl`}
                          type="number"
                          step="0.01"
                          value={form.realized_pnl}
                          onChange={(e) =>
                            updateLeg(leg.id, "realized_pnl", e.target.value)
                          }
                        />
                      </Field>
                    </div>
                  </div>
                );
              })
            )}
          </section>

          {mutation.error && (
            <div className="text-destructive mt-3 text-xs">
              {mutation.error.message}
            </div>
          )}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={mutation.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? (
                <>
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  Saving…
                </>
              ) : (
                "Save changes"
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div className="space-y-1">
      <label className="text-foreground text-xs font-medium" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
    </div>
  );
}
