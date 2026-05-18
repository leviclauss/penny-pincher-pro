import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  Code2,
  Copy,
  Loader2,
  Plus,
  Trash2,
} from "lucide-react";
import {
  ApiError,
  createScreenerConfig,
  fetchFilterCatalog,
  fetchScreenerConfig,
  fetchSectors,
  updateScreenerConfig,
} from "@/api/client";
import type {
  FilterCatalogEntry,
  FilterCategory,
  FilterParamSchema,
  ScreenerConfigDetail,
  ScreenerConfigWriteIn,
} from "@/api/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Checkbox } from "@/components/ui/Checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { cn } from "@/lib/utils";

// `sector_concentration` is a postprocessor (not in FILTER_REGISTRY) but the
// pipeline accepts it as a `filters[]` entry. The catalog endpoint omits it
// until the postprocessor catalog lands (doc 11 "Open questions"). The
// editor renders it as a known special-case so seed configs round-trip.
const POSTPROCESSOR_ENTRIES: Record<string, FilterCatalogEntry> = {
  sector_concentration: {
    id: "sector_concentration",
    label: "Sector concentration cap",
    description: "Cross-symbol postprocessor — limit results per sector.",
    category: "event",
    scored: false,
    params: [
      {
        name: "max",
        label: "Max per sector",
        kind: "integer",
        default: 3,
        min: 1,
        max: 50,
        step: 1,
        description: null,
      },
    ],
  },
};

const CATEGORY_LABELS: Record<FilterCategory, string> = {
  trend: "Trend",
  volatility: "Volatility",
  liquidity: "Liquidity",
  event: "Event",
};

const CATEGORY_TONES: Record<FilterCategory, string> = {
  trend: "bg-sky-500/10 text-sky-300 ring-sky-500/30",
  volatility: "bg-amber-500/10 text-amber-300 ring-amber-500/30",
  liquidity: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30",
  event: "bg-violet-500/10 text-violet-300 ring-violet-500/30",
};

type ParamValue = number | number[] | string[];

interface FormFilter {
  id: string;
  params: Record<string, ParamValue>;
  required: boolean;
}

interface FormState {
  name: string;
  description: string;
  is_active: boolean;
  filters: FormFilter[];
  weights: Record<string, number>;
}

const EMPTY_FORM: FormState = {
  name: "",
  description: "",
  is_active: true,
  filters: [],
  weights: {},
};

const CONFIGS_KEY = ["screener", "configs"] as const;

export function ScreenerConfigEditor(): JSX.Element {
  const params = useParams<{ id?: string }>();
  const isNew = !params.id || params.id === "new";
  const configId = isNew ? null : Number(params.id);
  const validId = configId !== null && Number.isFinite(configId);

  const navigate = useNavigate();
  const qc = useQueryClient();

  const catalogQuery = useQuery({
    queryKey: ["screener", "catalog"],
    queryFn: fetchFilterCatalog,
    staleTime: 5 * 60_000,
  });

  const detailQuery = useQuery({
    queryKey: ["screener", "config", configId],
    queryFn: () => fetchScreenerConfig(configId as number),
    enabled: validId,
  });

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const baselineRef = useRef<string>(JSON.stringify(EMPTY_FORM));
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [showJson, setShowJson] = useState(false);
  const [overflowOpen, setOverflowOpen] = useState(false);

  // Initialise form from server detail (or keep blank for new).
  useEffect(() => {
    if (isNew) {
      setForm(EMPTY_FORM);
      baselineRef.current = JSON.stringify(EMPTY_FORM);
      return;
    }
    if (detailQuery.data) {
      const next = formStateFromDetail(detailQuery.data);
      setForm(next);
      baselineRef.current = JSON.stringify(next);
    }
  }, [isNew, detailQuery.data]);

  const catalog = catalogQuery.data ?? [];
  const lookup = useMemo(() => {
    const out: Record<string, FilterCatalogEntry> = { ...POSTPROCESSOR_ENTRIES };
    for (const entry of catalog) out[entry.id] = entry;
    return out;
  }, [catalog]);

  const dirty = JSON.stringify(form) !== baselineRef.current;
  const errors = validate(form, lookup);
  const valid = errors.length === 0;

  const saveMutation = useMutation({
    mutationFn: (payload: ScreenerConfigWriteIn) =>
      validId
        ? updateScreenerConfig(configId as number, payload)
        : createScreenerConfig(payload),
    onSuccess: (saved: ScreenerConfigDetail) => {
      setSubmitError(null);
      void qc.invalidateQueries({ queryKey: CONFIGS_KEY });
      void qc.invalidateQueries({ queryKey: ["screener", "config", saved.id] });
      const next = formStateFromDetail(saved);
      setForm(next);
      baselineRef.current = JSON.stringify(next);
      if (isNew) navigate(`/screener/configs/${saved.id}`, { replace: true });
    },
    onError: (err: Error) => {
      setSubmitError(
        err instanceof ApiError ? extractErrorMessage(err) : err.message,
      );
    },
  });

  const duplicateMutation = useMutation({
    mutationFn: async () => {
      if (!validId || !detailQuery.data) {
        throw new Error("Save before duplicating");
      }
      const payload = buildDuplicatePayload(detailQuery.data);
      return createScreenerConfig(payload);
    },
    onSuccess: (saved) => {
      setOverflowOpen(false);
      void qc.invalidateQueries({ queryKey: CONFIGS_KEY });
      navigate(`/screener/configs/${saved.id}`);
    },
    onError: (err: Error) => setSubmitError(err.message),
  });

  const handleSave = (): void => {
    if (!valid) return;
    saveMutation.mutate(toWritePayload(form));
  };

  if (validId && detailQuery.isLoading) {
    return <div className="text-muted-foreground text-sm">Loading config…</div>;
  }
  if (validId && detailQuery.isError) {
    return (
      <div className="text-destructive text-sm">
        Failed to load config — it may have been deleted.
      </div>
    );
  }

  const headerTitle = isNew ? "New screener config" : form.name || "Untitled config";
  const sectorWeights = sumWeights(form.weights);

  return (
    <div className="space-y-6 pb-24">
      <header className="space-y-1.5">
        <p className="text-primary text-xs font-semibold uppercase tracking-widest">
          Strategy
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">{headerTitle}</h1>
        <p className="text-muted-foreground max-w-2xl text-sm">
          {isNew
            ? "Define which filters a candidate must clear and how to score the survivors."
            : "Tune filters, scoring weights, and metadata. Saving updates the active config in place."}
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Identity</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-1">
              <label className="text-foreground text-xs font-medium" htmlFor="cfg-name">
                Name *
              </label>
              <Input
                id="cfg-name"
                value={form.name}
                maxLength={128}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="e.g. Conservative Wheel — 200EMA Touch"
              />
            </div>
            <div className="space-y-1">
              <label className="text-foreground text-xs font-medium" htmlFor="cfg-active">
                Status
              </label>
              <div id="cfg-active" className="flex h-9 items-center">
                <Checkbox
                  label={form.is_active ? "Active" : "Inactive"}
                  checked={form.is_active}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, is_active: e.target.checked }))
                  }
                />
              </div>
            </div>
          </div>
          <div className="space-y-1">
            <label className="text-foreground text-xs font-medium" htmlFor="cfg-desc">
              Description
            </label>
            <textarea
              id="cfg-desc"
              value={form.description}
              onChange={(e) =>
                setForm((f) => ({ ...f, description: e.target.value }))
              }
              rows={2}
              placeholder="What this config is hunting for"
              className="border-border bg-background text-foreground focus-visible:ring-ring w-full rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <CardTitle>Filters ({form.filters.length})</CardTitle>
            <AddFilterPicker
              catalog={catalog}
              alreadyAdded={new Set(form.filters.map((f) => f.id))}
              onAdd={(entry) =>
                setForm((f) => ({
                  ...f,
                  filters: [...f.filters, blankFilterEntry(entry)],
                }))
              }
            />
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {form.filters.length === 0 && (
            <div className="text-muted-foreground text-sm">
              No filters yet. Use <strong>Add filter</strong> to compose the
              candidate set.
            </div>
          )}
          {form.filters.map((entry, idx) => (
            <FilterCard
              key={`${entry.id}-${idx}`}
              entry={entry}
              schema={lookup[entry.id]}
              isFirst={idx === 0}
              isLast={idx === form.filters.length - 1}
              onMoveUp={() =>
                setForm((f) => ({ ...f, filters: swap(f.filters, idx, idx - 1) }))
              }
              onMoveDown={() =>
                setForm((f) => ({ ...f, filters: swap(f.filters, idx, idx + 1) }))
              }
              onRemove={() =>
                setForm((f) => {
                  const filters = f.filters.filter((_, i) => i !== idx);
                  const weights = { ...f.weights };
                  delete weights[entry.id];
                  return { ...f, filters, weights };
                })
              }
              onChangeRequired={(required) =>
                setForm((f) => ({
                  ...f,
                  filters: f.filters.map((it, i) => (i === idx ? { ...it, required } : it)),
                }))
              }
              onChangeParam={(name, value) =>
                setForm((f) => ({
                  ...f,
                  filters: f.filters.map((it, i) =>
                    i === idx
                      ? { ...it, params: { ...it.params, [name]: value } }
                      : it,
                  ),
                }))
              }
            />
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <CardTitle>Scoring</CardTitle>
            <span className="text-muted-foreground font-mono text-xs">
              Σ = {sectorWeights.toFixed(2)}
            </span>
          </div>
        </CardHeader>
        <CardContent>
          <ScoringSection
            filters={form.filters}
            lookup={lookup}
            weights={form.weights}
            onChange={(id, value) =>
              setForm((f) => {
                const weights = { ...f.weights };
                if (value === null) delete weights[id];
                else weights[id] = value;
                return { ...f, weights };
              })
            }
          />
        </CardContent>
      </Card>

      <ValidationList errors={errors} />

      <div className="border-border bg-background/95 supports-[backdrop-filter]:bg-background/80 fixed inset-x-0 bottom-0 z-30 border-t backdrop-blur md:left-60">
        <div className="flex items-center justify-between gap-3 px-4 py-3 md:px-8">
          <div className="text-muted-foreground text-xs">
            {dirty ? "Unsaved changes" : "Up to date"}
            {submitError && (
              <span className="text-destructive ml-3">{submitError}</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <div className="relative">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setOverflowOpen((v) => !v)}
              >
                More
                <ChevronDown className="ml-1 h-3.5 w-3.5" />
              </Button>
              {overflowOpen && (
                <div className="border-border bg-popover absolute bottom-full right-0 mb-1 w-44 overflow-hidden rounded-md border shadow-lg">
                  <button
                    type="button"
                    className="hover:bg-accent flex w-full items-center gap-2 px-3 py-2 text-left text-sm disabled:opacity-50"
                    disabled={!validId || duplicateMutation.isPending}
                    onClick={() => duplicateMutation.mutate()}
                  >
                    {duplicateMutation.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" />
                    )}
                    Duplicate
                  </button>
                  <button
                    type="button"
                    className="hover:bg-accent flex w-full items-center gap-2 px-3 py-2 text-left text-sm"
                    onClick={() => {
                      setShowJson(true);
                      setOverflowOpen(false);
                    }}
                  >
                    <Code2 className="h-3.5 w-3.5" />
                    View as JSON
                  </button>
                </div>
              )}
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => navigate("/screener/configs")}
              disabled={saveMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={handleSave}
              disabled={!dirty || !valid || saveMutation.isPending}
            >
              {saveMutation.isPending ? (
                <>
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  Saving…
                </>
              ) : (
                "Save"
              )}
            </Button>
          </div>
        </div>
      </div>

      <Dialog open={showJson} onOpenChange={setShowJson}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Config JSON</DialogTitle>
            <DialogDescription>
              Exact payload that will be sent to the API.
            </DialogDescription>
          </DialogHeader>
          <pre className="bg-muted/60 max-h-[60vh] overflow-auto rounded-md p-3 font-mono text-xs">
            {JSON.stringify(toWritePayload(form), null, 2)}
          </pre>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowJson(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

interface FilterCardProps {
  entry: FormFilter;
  schema: FilterCatalogEntry | undefined;
  isFirst: boolean;
  isLast: boolean;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onRemove: () => void;
  onChangeRequired: (required: boolean) => void;
  onChangeParam: (name: string, value: ParamValue) => void;
}

function FilterCard({
  entry,
  schema,
  isFirst,
  isLast,
  onMoveUp,
  onMoveDown,
  onRemove,
  onChangeRequired,
  onChangeParam,
}: FilterCardProps): JSX.Element {
  const label = schema?.label ?? entry.id;
  const category = schema?.category;
  return (
    <div className="border-border bg-card/60 rounded-md border p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-0.5">
          <div className="flex items-center gap-2">
            <span className="text-foreground text-sm font-semibold">{label}</span>
            {category && (
              <span
                className={cn(
                  "rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ring-1",
                  CATEGORY_TONES[category],
                )}
              >
                {CATEGORY_LABELS[category]}
              </span>
            )}
            {!schema && (
              <Badge variant="warning">unknown id</Badge>
            )}
          </div>
          {schema?.description && (
            <p className="text-muted-foreground text-xs">{schema.description}</p>
          )}
          <p className="text-muted-foreground/70 font-mono text-[10px]">{entry.id}</p>
        </div>
        <div className="flex items-center gap-1">
          <Checkbox
            label="Required"
            checked={entry.required}
            onChange={(e) => onChangeRequired(e.target.checked)}
          />
          <Button
            variant="ghost"
            size="icon"
            title="Move up"
            onClick={onMoveUp}
            disabled={isFirst}
          >
            <ArrowUp className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            title="Move down"
            onClick={onMoveDown}
            disabled={isLast}
          >
            <ArrowDown className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="icon" title="Remove" onClick={onRemove}>
            <Trash2 className="text-destructive h-4 w-4" />
          </Button>
        </div>
      </div>

      {schema && schema.params.length > 0 && (
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {schema.params.map((spec) => (
            <ParamInput
              key={spec.name}
              spec={spec}
              value={entry.params[spec.name] ?? spec.default}
              onChange={(v) => onChangeParam(spec.name, v)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

interface ParamInputProps {
  spec: FilterParamSchema;
  value: ParamValue;
  onChange: (v: ParamValue) => void;
}

function ParamInput({ spec, value, onChange }: ParamInputProps): JSX.Element {
  if (spec.kind === "tier_set") {
    const selected = new Set<number>(
      Array.isArray(value) ? value.filter((v): v is number => typeof v === "number") : [],
    );
    const toggle = (tier: number): void => {
      const next = new Set(selected);
      if (next.has(tier)) next.delete(tier);
      else next.add(tier);
      onChange(Array.from(next).sort((a, b) => a - b));
    };
    return (
      <div className="space-y-1">
        <ParamLabel spec={spec} />
        <div className="flex gap-2">
          {[1, 2, 3, 4].map((t) => {
            const active = selected.has(t);
            return (
              <button
                key={t}
                type="button"
                onClick={() => toggle(t)}
                className={cn(
                  "border-border rounded-full border px-3 py-1 text-xs font-semibold transition-colors",
                  active
                    ? "border-primary bg-primary/15 text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent",
                )}
              >
                T{t}
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  if (spec.kind === "sector_set") {
    return <SectorMultiSelect spec={spec} value={value} onChange={onChange} />;
  }

  const isPercent = spec.kind === "percent";
  // For percent we display as % (multiply by 100); store raw fractional float.
  const displayValue =
    typeof value === "number"
      ? isPercent
        ? value * 100
        : value
      : 0;
  const displayMin = spec.min !== null ? (isPercent ? spec.min * 100 : spec.min) : undefined;
  const displayMax = spec.max !== null ? (isPercent ? spec.max * 100 : spec.max) : undefined;
  const displayStep = spec.step !== null
    ? isPercent ? spec.step * 100 : spec.step
    : spec.kind === "integer" ? 1 : undefined;

  return (
    <div className="space-y-1">
      <ParamLabel spec={spec} />
      <div className="flex items-center gap-1">
        <Input
          type="number"
          value={Number.isFinite(displayValue) ? displayValue : ""}
          min={displayMin}
          max={displayMax}
          step={displayStep}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") return;
            const n = Number(raw);
            if (!Number.isFinite(n)) return;
            const stored =
              spec.kind === "integer" ? Math.round(n) : isPercent ? n / 100 : n;
            onChange(stored);
          }}
        />
        {isPercent && <span className="text-muted-foreground text-xs">%</span>}
        {spec.kind === "currency" && (
          <span className="text-muted-foreground text-xs">$</span>
        )}
      </div>
    </div>
  );
}

function ParamLabel({ spec }: { spec: FilterParamSchema }): JSX.Element {
  return (
    <label className="text-foreground text-xs font-medium" title={spec.description ?? undefined}>
      {spec.label}
      <span className="text-muted-foreground/70 ml-1 font-mono text-[10px]">
        {spec.name}
      </span>
    </label>
  );
}

interface AddFilterPickerProps {
  catalog: FilterCatalogEntry[];
  alreadyAdded: Set<string>;
  onAdd: (entry: FilterCatalogEntry) => void;
}

function AddFilterPicker({
  catalog,
  alreadyAdded,
  onAdd,
}: AddFilterPickerProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const groups = useMemo(() => groupByCategory(catalog), [catalog]);
  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        <Plus className="mr-1 h-4 w-4" />
        Add filter
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add filter</DialogTitle>
          <DialogDescription>
            Pick from the registered filters. Already-added filters are disabled.
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-[60vh] space-y-4 overflow-auto">
          {(Object.keys(groups) as FilterCategory[]).map((cat) => (
            <div key={cat} className="space-y-2">
              <div className="text-muted-foreground flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest">
                <span
                  className={cn(
                    "rounded-full px-2 py-0.5 ring-1",
                    CATEGORY_TONES[cat],
                  )}
                >
                  {CATEGORY_LABELS[cat]}
                </span>
                <span>{groups[cat].length}</span>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {groups[cat].map((entry) => {
                  const taken = alreadyAdded.has(entry.id);
                  return (
                    <button
                      key={entry.id}
                      type="button"
                      disabled={taken}
                      onClick={() => {
                        onAdd(entry);
                        setOpen(false);
                      }}
                      className={cn(
                        "border-border bg-card/60 rounded-md border p-2 text-left transition-colors",
                        taken ? "opacity-40" : "hover:border-primary",
                      )}
                    >
                      <div className="text-sm font-semibold">{entry.label}</div>
                      <div className="text-muted-foreground text-xs">
                        {entry.description}
                      </div>
                      <div className="text-muted-foreground/60 mt-1 font-mono text-[10px]">
                        {entry.id}
                        {!entry.scored && " · unscored"}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
      </Dialog>
    </>
  );
}

interface ScoringSectionProps {
  filters: FormFilter[];
  lookup: Record<string, FilterCatalogEntry>;
  weights: Record<string, number>;
  onChange: (id: string, value: number | null) => void;
}

function ScoringSection({
  filters,
  lookup,
  weights,
  onChange,
}: ScoringSectionProps): JSX.Element {
  const eligible = filters
    .map((f) => ({ entry: f, schema: lookup[f.id] }))
    .filter((e) => e.schema?.scored === true);

  if (eligible.length === 0) {
    return (
      <div className="text-muted-foreground text-sm">
        No scored filters in this config. Add a scored filter (e.g. Near 200 EMA,
        IV Rank) to enable scoring.
      </div>
    );
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {eligible.map(({ entry, schema }) => {
        const current = weights[entry.id];
        return (
          <div key={entry.id} className="space-y-1">
            <label
              htmlFor={`weight-${entry.id}`}
              className="text-foreground text-xs font-medium"
            >
              {schema?.label ?? entry.id}
              <span className="text-muted-foreground/70 ml-1 font-mono text-[10px]">
                {entry.id}
              </span>
            </label>
            <Input
              id={`weight-${entry.id}`}
              type="number"
              min={0}
              step={0.05}
              value={current ?? ""}
              placeholder="0"
              onChange={(e) => {
                const raw = e.target.value;
                if (raw === "") {
                  onChange(entry.id, null);
                  return;
                }
                const n = Number(raw);
                if (!Number.isFinite(n)) return;
                onChange(entry.id, n);
              }}
            />
          </div>
        );
      })}
    </div>
  );
}

function ValidationList({ errors }: { errors: string[] }): JSX.Element | null {
  if (errors.length === 0) return null;
  return (
    <Card className="border-destructive/40 bg-destructive/5">
      <CardContent className="space-y-1 py-3">
        <div className="text-destructive text-xs font-semibold uppercase tracking-wider">
          Fix before saving
        </div>
        <ul className="text-destructive list-disc space-y-0.5 pl-5 text-xs">
          {errors.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

interface SectorMultiSelectProps {
  spec: FilterParamSchema;
  value: ParamValue;
  onChange: (v: ParamValue) => void;
}

function SectorMultiSelect({
  spec,
  value,
  onChange,
}: SectorMultiSelectProps): JSX.Element {
  const { data: sectors, isLoading } = useQuery({
    queryKey: ["sectors"],
    queryFn: fetchSectors,
  });
  const selected = new Set<string>(
    Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [],
  );
  const toggle = (sector: string): void => {
    const next = new Set(selected);
    if (next.has(sector)) next.delete(sector);
    else next.add(sector);
    onChange(Array.from(next).sort());
  };
  const options = sectors ?? [];

  return (
    <div className="space-y-1">
      <ParamLabel spec={spec} />
      {isLoading && (
        <p className="text-muted-foreground text-xs">Loading sectors…</p>
      )}
      {!isLoading && options.length === 0 && (
        <p className="text-muted-foreground text-xs">
          No sectors yet — run <code>ingestion.ticker_metadata</code> to populate.
        </p>
      )}
      {options.length > 0 && (
        <>
          <div className="flex flex-wrap gap-2">
            {options.map((s) => {
              const active = selected.has(s);
              return (
                <button
                  key={s}
                  type="button"
                  onClick={() => toggle(s)}
                  className={cn(
                    "border-border rounded-full border px-3 py-1 text-xs font-semibold transition-colors",
                    active
                      ? "border-primary bg-primary/15 text-primary-foreground"
                      : "text-muted-foreground hover:bg-accent",
                  )}
                >
                  {s}
                </button>
              );
            })}
          </div>
          <p className="text-muted-foreground text-[10px]">
            Empty = no restriction (every sector passes).
          </p>
        </>
      )}
    </div>
  );
}

// ---------- helpers ----------

function blankFilterEntry(entry: FilterCatalogEntry): FormFilter {
  const params: Record<string, ParamValue> = {};
  for (const p of entry.params) {
    const d = p.default;
    if (Array.isArray(d)) {
      if (d.every((x): x is string => typeof x === "string")) {
        params[p.name] = [...d];
      } else if (d.every((x): x is number => typeof x === "number")) {
        params[p.name] = [...d];
      } else {
        params[p.name] = [];
      }
    } else {
      params[p.name] = d;
    }
  }
  return { id: entry.id, params, required: false };
}

function swap<T>(arr: T[], a: number, b: number): T[] {
  if (a < 0 || b < 0 || a >= arr.length || b >= arr.length) return arr;
  const out = [...arr];
  const tmp = out[a];
  out[a] = out[b];
  out[b] = tmp;
  return out;
}

function groupByCategory(
  catalog: FilterCatalogEntry[],
): Record<FilterCategory, FilterCatalogEntry[]> {
  const out: Record<FilterCategory, FilterCatalogEntry[]> = {
    trend: [],
    volatility: [],
    liquidity: [],
    event: [],
  };
  for (const entry of catalog) out[entry.category].push(entry);
  return out;
}

function sumWeights(weights: Record<string, number>): number {
  return Object.values(weights).reduce((s, n) => s + (Number.isFinite(n) ? n : 0), 0);
}

function formStateFromDetail(detail: ScreenerConfigDetail): FormState {
  const raw = detail.config_json ?? {};
  const filtersRaw = Array.isArray(raw.filters) ? raw.filters : [];
  const filters: FormFilter[] = filtersRaw
    .filter((f): f is Record<string, unknown> => typeof f === "object" && f !== null)
    .map((f) => {
      const id = String(f.id ?? "");
      const paramsRaw =
        typeof f.params === "object" && f.params !== null
          ? (f.params as Record<string, unknown>)
          : {};
      const params: Record<string, ParamValue> = {};
      for (const [k, v] of Object.entries(paramsRaw)) {
        if (typeof v === "number") params[k] = v;
        else if (Array.isArray(v) && v.every((x) => typeof x === "number")) {
          params[k] = v as number[];
        } else if (Array.isArray(v) && v.every((x) => typeof x === "string")) {
          params[k] = v as string[];
        }
      }
      return { id, params, required: Boolean(f.required) };
    })
    .filter((f) => f.id);

  const scoringRaw =
    typeof raw.scoring === "object" && raw.scoring !== null
      ? (raw.scoring as { weights?: Record<string, unknown> })
      : {};
  const weights: Record<string, number> = {};
  for (const [k, v] of Object.entries(scoringRaw.weights ?? {})) {
    if (typeof v === "number" && Number.isFinite(v)) weights[k] = v;
  }

  return {
    name: detail.name,
    description: detail.description ?? "",
    is_active: detail.is_active,
    filters,
    weights,
  };
}

function toWritePayload(form: FormState): ScreenerConfigWriteIn {
  const description = form.description.trim() === "" ? null : form.description;
  return {
    name: form.name.trim(),
    description,
    is_active: form.is_active,
    filters: form.filters.map((f) => ({
      id: f.id,
      params: f.params as Record<string, unknown>,
      required: f.required,
    })),
    scoring: { weights: { ...form.weights } },
  };
}

function buildDuplicatePayload(detail: ScreenerConfigDetail): ScreenerConfigWriteIn {
  const form = formStateFromDetail(detail);
  return {
    ...toWritePayload(form),
    name: `${detail.name} (copy)`,
    is_active: false,
  };
}

function validate(
  form: FormState,
  lookup: Record<string, FilterCatalogEntry>,
): string[] {
  const errs: string[] = [];
  if (form.name.trim() === "") errs.push("Name is required.");
  if (form.name.length > 128) errs.push("Name must be 128 characters or fewer.");
  if (form.filters.length === 0) errs.push("At least one filter is required.");

  const seen = new Set<string>();
  for (const entry of form.filters) {
    if (seen.has(entry.id)) errs.push(`Duplicate filter: ${entry.id}.`);
    seen.add(entry.id);
    const schema = lookup[entry.id];
    if (!schema) {
      errs.push(`Unknown filter id: ${entry.id}.`);
      continue;
    }
    for (const spec of schema.params) {
      const v = entry.params[spec.name] ?? spec.default;
      const paramErr = checkParam(entry.id, spec, v);
      if (paramErr) errs.push(paramErr);
    }
  }

  for (const [id, weight] of Object.entries(form.weights)) {
    if (!seen.has(id)) {
      errs.push(`Weight references missing filter: ${id}.`);
      continue;
    }
    const schema = lookup[id];
    if (!schema?.scored) {
      errs.push(`Filter ${id} is not scored and cannot be weighted.`);
    }
    if (!Number.isFinite(weight) || weight < 0) {
      errs.push(`Weight for ${id} must be ≥ 0.`);
    }
  }
  return errs;
}

function checkParam(
  filterId: string,
  spec: FilterParamSchema,
  value: ParamValue,
): string | null {
  if (spec.kind === "tier_set") {
    if (!Array.isArray(value)) return `${filterId}.${spec.name}: expected list of tiers.`;
    for (const t of value) {
      if (typeof t !== "number" || ![1, 2, 3, 4].includes(t)) {
        return `${filterId}.${spec.name}: tier ${t} not in [1, 2, 3, 4].`;
      }
    }
    return null;
  }
  if (spec.kind === "sector_set") {
    if (!Array.isArray(value)) return `${filterId}.${spec.name}: expected list of sectors.`;
    for (const s of value) {
      if (typeof s !== "string") {
        return `${filterId}.${spec.name}: sector entries must be strings.`;
      }
    }
    return null;
  }
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return `${filterId}.${spec.name}: must be a number.`;
  }
  if (spec.kind === "integer" && !Number.isInteger(value)) {
    return `${filterId}.${spec.name}: must be an integer.`;
  }
  if (spec.min !== null && value < spec.min) {
    return `${filterId}.${spec.name}: ${value} below min ${spec.min}.`;
  }
  if (spec.max !== null && value > spec.max) {
    return `${filterId}.${spec.name}: ${value} above max ${spec.max}.`;
  }
  return null;
}

function extractErrorMessage(err: ApiError): string {
  if (typeof err.detail === "string") return err.detail;
  if (err.detail && typeof err.detail === "object" && "message" in err.detail) {
    return String((err.detail as { message: unknown }).message);
  }
  return err.message;
}
