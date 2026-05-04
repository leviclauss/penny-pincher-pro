import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Copy, Loader2, Pencil, Plus, Trash2 } from "lucide-react";
import {
  ApiError,
  createScreenerConfig,
  deleteScreenerConfig,
  fetchScreenerConfig,
  fetchScreenerConfigs,
  patchScreenerConfigActive,
} from "@/api/client";
import type { ScreenerConfigSummary, ScreenerConfigWriteIn } from "@/api/types";
import { Button, buttonVariants } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/Table";
import { cn } from "@/lib/utils";
import { formatDate } from "@/lib/format";

const CONFIGS_KEY = ["screener", "configs"] as const;

interface DeleteState {
  config: ScreenerConfigSummary;
  conflictCount: number | null;
  cascading: boolean;
}

export function ScreenerConfigs(): JSX.Element {
  const qc = useQueryClient();
  const [deleteTarget, setDeleteTarget] = useState<DeleteState | null>(null);
  const [duplicateError, setDuplicateError] = useState<string | null>(null);

  const configsQuery = useQuery({
    queryKey: CONFIGS_KEY,
    queryFn: () => fetchScreenerConfigs(false),
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, isActive }: { id: number; isActive: boolean }) =>
      patchScreenerConfigActive(id, isActive),
    onMutate: async ({ id, isActive }) => {
      await qc.cancelQueries({ queryKey: CONFIGS_KEY });
      const previous = qc.getQueryData<ScreenerConfigSummary[]>(CONFIGS_KEY);
      if (previous) {
        qc.setQueryData<ScreenerConfigSummary[]>(
          CONFIGS_KEY,
          previous.map((c) => (c.id === id ? { ...c, is_active: isActive } : c)),
        );
      }
      return { previous };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(CONFIGS_KEY, ctx.previous);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: CONFIGS_KEY });
    },
  });

  const duplicateMutation = useMutation({
    mutationFn: async (id: number) => {
      const detail = await fetchScreenerConfig(id);
      const payload = buildDuplicatePayload(detail.config_json, detail.name);
      return createScreenerConfig(payload);
    },
    onSuccess: () => {
      setDuplicateError(null);
      void qc.invalidateQueries({ queryKey: CONFIGS_KEY });
    },
    onError: (err: Error) => setDuplicateError(err.message),
  });

  const deleteMutation = useMutation({
    mutationFn: ({ id, cascade }: { id: number; cascade: boolean }) =>
      deleteScreenerConfig(id, cascade),
    onSuccess: () => {
      setDeleteTarget(null);
      void qc.invalidateQueries({ queryKey: CONFIGS_KEY });
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) {
        const detail = err.detail;
        const count =
          detail && typeof detail === "object" && "result_count" in detail
            ? Number((detail as { result_count: unknown }).result_count) || null
            : null;
        setDeleteTarget((prev) =>
          prev ? { ...prev, conflictCount: count, cascading: false } : prev,
        );
      }
    },
  });

  const configs = configsQuery.data ?? [];

  return (
    <div className="space-y-6">
      <header className="space-y-1.5">
        <p className="text-primary text-xs font-semibold uppercase tracking-widest">
          Strategy
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">Screener configs</h1>
        <p className="text-muted-foreground max-w-2xl text-sm">
          Tune which filters define a wheel candidate. Configs marked active are
          evaluated by the nightly screener pass.
        </p>
      </header>

      <Card>
        <CardHeader className="px-3 sm:px-5">
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
            <div className="flex items-center gap-3">
              <CardTitle>All configs</CardTitle>
              <span className="text-muted-foreground text-xs">
                {configsQuery.data ? `${configsQuery.data.length} configs` : "—"}
              </span>
            </div>
            <Link to="/screener/configs/new" className={buttonVariants({ size: "sm" })}>
              <Plus className="mr-1 h-4 w-4" />
              New config
            </Link>
          </div>
        </CardHeader>
        <CardContent className="px-3 pb-3 sm:px-5 sm:pb-5">
          {configsQuery.isLoading && (
            <div className="text-muted-foreground text-sm">Loading…</div>
          )}
          {configsQuery.isError && (
            <div className="text-destructive text-sm">Failed to load configs.</div>
          )}
          {configsQuery.data && configsQuery.data.length === 0 && (
            <div className="text-muted-foreground text-sm">
              No filter configs yet. Click <strong>New config</strong> to add one
              (or run{" "}
              <code className="font-mono">python -m scripts.seed_filter_configs</code>
              {" "}to seed the default).
            </div>
          )}
          {duplicateError && (
            <div className="text-destructive mb-3 text-xs">{duplicateError}</div>
          )}
          {configs.length > 0 && (
            <>
              <ul className="divide-border/50 -mx-1 divide-y md:hidden">
                {configs.map((c) => (
                  <ConfigMobileCard
                    key={c.id}
                    config={c}
                    onToggleActive={(isActive) =>
                      toggleMutation.mutate({ id: c.id, isActive })
                    }
                    onDuplicate={() => duplicateMutation.mutate(c.id)}
                    onDelete={() =>
                      setDeleteTarget({ config: c, conflictCount: null, cascading: false })
                    }
                    duplicating={
                      duplicateMutation.isPending && duplicateMutation.variables === c.id
                    }
                  />
                ))}
              </ul>
              <div className="hidden md:block">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Name</TableHead>
                      <TableHead>Description</TableHead>
                      <TableHead className="text-right"># Filters</TableHead>
                      <TableHead className="text-right">Active</TableHead>
                      <TableHead className="text-right">Updated</TableHead>
                      <TableHead className="text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {configs.map((c) => (
                      <ConfigRow
                        key={c.id}
                        config={c}
                        onToggleActive={(isActive) =>
                          toggleMutation.mutate({ id: c.id, isActive })
                        }
                        onDuplicate={() => duplicateMutation.mutate(c.id)}
                        onDelete={() =>
                          setDeleteTarget({ config: c, conflictCount: null, cascading: false })
                        }
                        duplicating={
                          duplicateMutation.isPending && duplicateMutation.variables === c.id
                        }
                      />
                    ))}
                  </TableBody>
                </Table>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <DeleteConfigDialog
        target={deleteTarget}
        pending={deleteMutation.isPending}
        onClose={() => {
          setDeleteTarget(null);
          deleteMutation.reset();
        }}
        onDeactivate={(id) => {
          toggleMutation.mutate({ id, isActive: false });
          setDeleteTarget(null);
        }}
        onConfirm={(id, cascade) => {
          setDeleteTarget((prev) => (prev ? { ...prev, cascading: cascade } : prev));
          deleteMutation.mutate({ id, cascade });
        }}
      />
    </div>
  );
}

interface ConfigRowProps {
  config: ScreenerConfigSummary;
  onToggleActive: (isActive: boolean) => void;
  onDuplicate: () => void;
  onDelete: () => void;
  duplicating: boolean;
}

function ConfigRow({
  config,
  onToggleActive,
  onDuplicate,
  onDelete,
  duplicating,
}: ConfigRowProps): JSX.Element {
  return (
    <TableRow className={cn(!config.is_active && "opacity-60")}>
      <TableCell className="font-semibold tracking-tight">{config.name}</TableCell>
      <TableCell className="text-muted-foreground max-w-[360px] truncate text-sm">
        {config.description ?? "—"}
      </TableCell>
      <TableCell className="text-right font-mono text-xs">
        {config.filter_ids.length}
      </TableCell>
      <TableCell className="text-right">
        <ActiveToggle
          checked={config.is_active}
          onChange={(next) => onToggleActive(next)}
        />
      </TableCell>
      <TableCell className="text-muted-foreground text-right font-mono text-xs">
        {formatDate(config.updated_at)}
      </TableCell>
      <TableCell className="text-right">
        <div className="flex justify-end gap-1">
          <Link
            to={`/screener/configs/${config.id}`}
            title="Edit"
            className={buttonVariants({ variant: "ghost", size: "icon" })}
          >
            <Pencil className="h-4 w-4" />
          </Link>
          <Button
            variant="ghost"
            size="icon"
            title="Duplicate"
            onClick={onDuplicate}
            disabled={duplicating}
          >
            {duplicating ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Copy className="h-4 w-4" />
            )}
          </Button>
          <Button variant="ghost" size="icon" title="Delete" onClick={onDelete}>
            <Trash2 className="text-destructive h-4 w-4" />
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}

function ConfigMobileCard({
  config,
  onToggleActive,
  onDuplicate,
  onDelete,
  duplicating,
}: ConfigRowProps): JSX.Element {
  return (
    <li className={cn("px-1 py-3", !config.is_active && "opacity-60")}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-base font-semibold tracking-tight">{config.name}</div>
          {config.description && (
            <div className="text-muted-foreground mt-0.5 line-clamp-2 text-xs">
              {config.description}
            </div>
          )}
          <div className="text-muted-foreground mt-1 flex flex-wrap items-center gap-x-3 font-mono text-[11px]">
            <span>{config.filter_ids.length} filters</span>
            <span>{formatDate(config.updated_at)}</span>
          </div>
        </div>
        <div className="shrink-0">
          <ActiveToggle
            checked={config.is_active}
            onChange={(next) => onToggleActive(next)}
          />
        </div>
      </div>
      <div className="mt-2 flex items-center justify-end gap-1">
        <Link
          to={`/screener/configs/${config.id}`}
          title="Edit"
          className={buttonVariants({ variant: "ghost", size: "icon" })}
        >
          <Pencil className="h-4 w-4" />
        </Link>
        <Button
          variant="ghost"
          size="icon"
          title="Duplicate"
          onClick={onDuplicate}
          disabled={duplicating}
        >
          {duplicating ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Copy className="h-4 w-4" />
          )}
        </Button>
        <Button variant="ghost" size="icon" title="Delete" onClick={onDelete}>
          <Trash2 className="text-destructive h-4 w-4" />
        </Button>
      </div>
    </li>
  );
}

interface ActiveToggleProps {
  checked: boolean;
  onChange: (next: boolean) => void;
}

function ActiveToggle({ checked, onChange }: ActiveToggleProps): JSX.Element {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={cn(
        "focus-visible:ring-ring relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2",
        checked ? "bg-emerald-500/70" : "bg-muted",
      )}
    >
      <span
        className={cn(
          "bg-background inline-block h-4 w-4 transform rounded-full shadow transition-transform",
          checked ? "translate-x-4" : "translate-x-0.5",
        )}
      />
    </button>
  );
}

interface DeleteConfigDialogProps {
  target: DeleteState | null;
  pending: boolean;
  onClose: () => void;
  onDeactivate: (id: number) => void;
  onConfirm: (id: number, cascade: boolean) => void;
}

function DeleteConfigDialog({
  target,
  pending,
  onClose,
  onDeactivate,
  onConfirm,
}: DeleteConfigDialogProps): JSX.Element {
  const open = target !== null;
  const hasConflict = target?.conflictCount !== null && target?.conflictCount !== undefined;
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete {target?.config.name}?</DialogTitle>
          <DialogDescription>
            {hasConflict ? (
              <>
                This config has <strong>{target?.conflictCount}</strong> screener
                results referencing it. Deactivate to keep the history, or force
                delete to wipe both the config and its results.
              </>
            ) : (
              <>
                Hard-deletes the config. If any{" "}
                <code className="font-mono">screener_results</code> reference it
                you'll get the option to deactivate instead.
              </>
            )}
          </DialogDescription>
        </DialogHeader>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          {hasConflict && target?.config.is_active && (
            <Button
              variant="secondary"
              onClick={() => onDeactivate(target.config.id)}
              disabled={pending}
            >
              Deactivate instead
            </Button>
          )}
          <Button
            variant="destructive"
            onClick={() => target && onConfirm(target.config.id, hasConflict)}
            disabled={pending}
          >
            {pending ? (
              <>
                <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                {target?.cascading ? "Force deleting…" : "Deleting…"}
              </>
            ) : hasConflict ? (
              "Force delete (cascade)"
            ) : (
              "Delete"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function buildDuplicatePayload(
  configJson: Record<string, unknown>,
  originalName: string,
): ScreenerConfigWriteIn {
  const filtersRaw = Array.isArray(configJson.filters) ? configJson.filters : [];
  const filters = filtersRaw
    .filter((f): f is Record<string, unknown> => typeof f === "object" && f !== null)
    .map((f) => ({
      id: String(f.id ?? ""),
      params: (f.params as Record<string, unknown> | undefined) ?? {},
      required: Boolean(f.required),
    }))
    .filter((f) => f.id);

  const scoringRaw =
    typeof configJson.scoring === "object" && configJson.scoring !== null
      ? (configJson.scoring as { weights?: Record<string, number> })
      : {};

  return {
    name: `${originalName} (copy)`,
    description:
      typeof configJson.description === "string" ? configJson.description : null,
    is_active: false,
    filters,
    scoring: { weights: { ...(scoringRaw.weights ?? {}) } },
  };
}
