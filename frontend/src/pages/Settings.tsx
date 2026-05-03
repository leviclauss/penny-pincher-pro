import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Play, Save } from "lucide-react";
import {
  fetchAlertPreferences,
  fetchChannels,
  fetchJobs,
  triggerJob,
  updateAlertPreference,
} from "@/api/client";
import type {
  AlertPreference,
  AlertPreferenceUpdate,
  JobInfoOut,
} from "@/api/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
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
import { formatDateTime } from "@/lib/format";

const KNOWN_CHANNELS = ["telegram", "email", "ntfy"] as const;

function dirty(a: AlertPreference, b: AlertPreference): boolean {
  return (
    a.enabled !== b.enabled ||
    a.quiet_hours_start !== b.quiet_hours_start ||
    a.quiet_hours_end !== b.quiet_hours_end ||
    a.channels.length !== b.channels.length ||
    a.channels.some((c) => !b.channels.includes(c))
  );
}

function PreferenceRow({ pref }: { pref: AlertPreference }): JSX.Element {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<AlertPreference>(pref);

  // Reset local draft when the server payload changes (e.g. after an external save).
  useEffect(() => setDraft(pref), [pref]);

  const isDirty = useMemo(() => dirty(draft, pref), [draft, pref]);

  const save = useMutation({
    mutationFn: () => {
      const payload: AlertPreferenceUpdate = {
        channels: draft.channels,
        enabled: draft.enabled,
        quiet_hours_start: draft.quiet_hours_start || null,
        quiet_hours_end: draft.quiet_hours_end || null,
      };
      return updateAlertPreference(draft.alert_type, payload);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["alert-preferences"] });
    },
  });

  const toggleChannel = (channel: string, on: boolean): void => {
    setDraft((prev) => {
      const next = on
        ? Array.from(new Set([...prev.channels, channel]))
        : prev.channels.filter((c) => c !== channel);
      return { ...prev, channels: next };
    });
  };

  return (
    <TableRow>
      <TableCell>
        <div className="font-medium">{draft.alert_type}</div>
      </TableCell>
      <TableCell>
        <Checkbox
          checked={draft.enabled}
          onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
        />
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap gap-3">
          {KNOWN_CHANNELS.map((channel) => (
            <Checkbox
              key={channel}
              checked={draft.channels.includes(channel)}
              onChange={(e) => toggleChannel(channel, e.target.checked)}
              label={channel}
            />
          ))}
        </div>
      </TableCell>
      <TableCell>
        <Input
          type="time"
          value={draft.quiet_hours_start ?? ""}
          onChange={(e) =>
            setDraft({ ...draft, quiet_hours_start: e.target.value || null })
          }
          className="w-28"
        />
      </TableCell>
      <TableCell>
        <Input
          type="time"
          value={draft.quiet_hours_end ?? ""}
          onChange={(e) =>
            setDraft({ ...draft, quiet_hours_end: e.target.value || null })
          }
          className="w-28"
        />
      </TableCell>
      <TableCell>
        <Button
          size="sm"
          variant="outline"
          disabled={!isDirty || save.isPending}
          onClick={() => save.mutate()}
        >
          {save.isPending ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Save className="h-3 w-3" />
          )}
          <span className="ml-1.5">Save</span>
        </Button>
        {save.isError ? (
          <div className="text-destructive mt-1 text-[11px]">
            {save.error instanceof Error ? save.error.message : "save failed"}
          </div>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

function AlertPreferencesCard(): JSX.Element {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["alert-preferences"],
    queryFn: fetchAlertPreferences,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Alert preferences</CardTitle>
      </CardHeader>
      <CardContent className="px-0">
        {isLoading ? (
          <div className="text-muted-foreground px-5 py-8 text-center text-sm">
            Loading…
          </div>
        ) : isError ? (
          <div className="text-destructive px-5 py-8 text-center text-sm">
            Failed to load alert preferences.
          </div>
        ) : !data || data.length === 0 ? (
          <div className="text-muted-foreground px-5 py-8 text-center text-sm">
            No alert types registered.
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Alert type</TableHead>
                <TableHead>Enabled</TableHead>
                <TableHead>Channels</TableHead>
                <TableHead>Quiet start</TableHead>
                <TableHead>Quiet end</TableHead>
                <TableHead></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((pref) => (
                <PreferenceRow key={pref.alert_type} pref={pref} />
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function DataFreshnessCard(): JSX.Element {
  const qc = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["jobs"],
    queryFn: fetchJobs,
    refetchInterval: 10_000,
  });

  const trigger = useMutation({
    mutationFn: () => triggerJob("evening_pipeline"),
    onSettled: () => {
      setTimeout(() => {
        void qc.invalidateQueries({ queryKey: ["jobs"] });
      }, 1500);
    },
  });

  const eveningJob: JobInfoOut | undefined = data?.find(
    (j) => j.name === "evening_pipeline",
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>Data freshness</CardTitle>
      </CardHeader>
      <CardContent className="px-0">
        {isLoading ? (
          <div className="text-muted-foreground px-5 py-8 text-center text-sm">
            Loading…
          </div>
        ) : isError ? (
          <div className="text-destructive px-5 py-8 text-center text-sm">
            Failed to load jobs.
          </div>
        ) : !data || data.length === 0 ? (
          <div className="text-muted-foreground px-5 py-8 text-center text-sm">
            No jobs registered.
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Job</TableHead>
                <TableHead>Last status</TableHead>
                <TableHead>Last run</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((job) => (
                <TableRow key={job.name}>
                  <TableCell>
                    <div className="font-medium">{job.name}</div>
                    <div className="text-muted-foreground text-xs">
                      {job.description}
                    </div>
                  </TableCell>
                  <TableCell>
                    {job.last_run ? (
                      <Badge
                        variant={
                          job.last_run.status === "success"
                            ? "success"
                            : job.last_run.status === "failure"
                              ? "destructive"
                              : "warning"
                        }
                      >
                        {job.last_run.status}
                      </Badge>
                    ) : (
                      <Badge variant="default">never run</Badge>
                    )}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {formatDateTime(job.last_run?.started_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <div className="px-5 pt-4">
          <Button
            size="sm"
            variant="outline"
            disabled={!eveningJob || trigger.isPending}
            onClick={() => trigger.mutate()}
            title={
              eveningJob
                ? "Re-run the evening ingestion pipeline now"
                : "evening_pipeline job not registered"
            }
          >
            {trigger.isPending ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Play className="h-3 w-3" />
            )}
            <span className="ml-1.5">Run ingestion now</span>
          </Button>
          {trigger.isError ? (
            <div className="text-destructive mt-2 text-[11px]">
              {trigger.error instanceof Error
                ? trigger.error.message
                : "trigger failed"}
            </div>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

function ChannelStatusRow({
  label,
  configured,
  envHint,
}: {
  label: string;
  configured: boolean;
  envHint: string;
}): JSX.Element {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="text-sm font-medium">{label}:</span>
      {configured ? (
        <Badge variant="success">configured</Badge>
      ) : (
        <Badge variant="warning">not configured</Badge>
      )}
      <span className="text-muted-foreground text-xs">
        Set <code className="font-mono">{envHint}</code> in the backend env to
        enable delivery.
      </span>
    </div>
  );
}

function ChannelsCard(): JSX.Element {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["channels"],
    queryFn: fetchChannels,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Channels</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="text-muted-foreground py-2 text-sm">Loading…</div>
        ) : isError || !data ? (
          <div className="text-destructive py-2 text-sm">
            Failed to load channel status.
          </div>
        ) : (
          <div className="space-y-2">
            <ChannelStatusRow
              label="Telegram"
              configured={data.telegram}
              envHint="TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID"
            />
            <ChannelStatusRow
              label="Email"
              configured={data.email}
              envHint="SMTP_HOST + SMTP_FROM_ADDRESS + SMTP_TO_ADDRESS"
            />
            <ChannelStatusRow
              label="ntfy"
              configured={data.ntfy}
              envHint="NTFY_TOPIC (server defaults to https://ntfy.sh)"
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function Settings(): JSX.Element {
  return (
    <div className="space-y-8">
      <header className="space-y-1.5">
        <p className="text-primary text-xs font-semibold uppercase tracking-widest">
          System
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">Settings</h1>
        <p className="text-muted-foreground max-w-2xl text-sm">
          Configure how alerts are delivered, see when ingestion last ran, and
          check which channels are wired up.
        </p>
      </header>

      <AlertPreferencesCard />
      <DataFreshnessCard />
      <ChannelsCard />
    </div>
  );
}
