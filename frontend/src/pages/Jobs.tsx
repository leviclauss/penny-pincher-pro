import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Play } from "lucide-react";
import { fetchJobRuns, fetchJobs, triggerJob } from "@/api/client";
import type { JobInfoOut, JobRunOut } from "@/api/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
import { formatDateTime } from "@/lib/format";

function StatusBadge({ status }: { status: JobRunOut["status"] | null }): JSX.Element {
  if (status === null) return <Badge variant="default">never run</Badge>;
  if (status === "success") return <Badge variant="success">success</Badge>;
  if (status === "failure") return <Badge variant="destructive">failure</Badge>;
  return <Badge variant="warning">running</Badge>;
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds - m * 60);
  return `${m}m ${s}s`;
}

function ResultMetrics({ result }: { result: Record<string, unknown> | null }): JSX.Element {
  if (!result || Object.keys(result).length === 0) {
    return <span className="text-muted-foreground">—</span>;
  }
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[11px]">
      {Object.entries(result).map(([k, v]) => (
        <span key={k}>
          <span className="text-muted-foreground">{k}:</span>{" "}
          <span className="text-foreground">{String(v)}</span>
        </span>
      ))}
    </div>
  );
}

function JobHistoryDialog({
  jobName,
  open,
  onOpenChange,
}: {
  jobName: string | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}): JSX.Element | null {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["job-runs", jobName],
    queryFn: () => fetchJobRuns(jobName ?? "", 50),
    enabled: open && jobName !== null,
    refetchInterval: open ? 4000 : false,
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>{jobName ?? ""} — run history</DialogTitle>
          <DialogDescription>Most recent 50 executions.</DialogDescription>
        </DialogHeader>
        {isLoading ? (
          <div className="text-muted-foreground py-8 text-center text-sm">Loading…</div>
        ) : isError ? (
          <div className="text-destructive py-8 text-center text-sm">
            Failed to load run history.
          </div>
        ) : !data || data.length === 0 ? (
          <div className="text-muted-foreground py-8 text-center text-sm">
            No runs recorded yet.
          </div>
        ) : (
          <div className="max-h-[60vh] overflow-auto overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Status</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>Result</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.map((run) => (
                  <TableRow key={run.id}>
                    <TableCell>
                      <StatusBadge status={run.status} />
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {formatDateTime(run.started_at)}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {formatDuration(run.duration_s)}
                    </TableCell>
                    <TableCell>
                      {run.error ? (
                        <span className="text-destructive font-mono text-[11px]">
                          {run.error}
                        </span>
                      ) : (
                        <ResultMetrics result={run.result_json} />
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function useTriggerJob(job: JobInfoOut) {
  const qc = useQueryClient();
  const [pendingTrigger, setPendingTrigger] = useState(false);
  const isRunning = job.last_run?.status === "running";

  const trigger = useMutation({
    mutationFn: () => triggerJob(job.name),
    onMutate: () => setPendingTrigger(true),
    onSettled: () => {
      setTimeout(() => {
        setPendingTrigger(false);
        void qc.invalidateQueries({ queryKey: ["jobs"] });
        void qc.invalidateQueries({ queryKey: ["job-runs"] });
      }, 1500);
    },
  });

  const triggerDisabled =
    !job.enabled || pendingTrigger || isRunning || trigger.isPending;
  const triggerTitle = !job.enabled
    ? "Scheduler is disabled — restart with SCHEDULER_ENABLED=true"
    : isRunning
      ? "Already running"
      : "Run this job now";
  const triggerIconBusy = pendingTrigger || isRunning;

  return { trigger, triggerDisabled, triggerTitle, triggerIconBusy };
}

function JobRow({
  job,
  onOpenHistory,
}: {
  job: JobInfoOut;
  onOpenHistory: (name: string) => void;
}): JSX.Element {
  const { trigger, triggerDisabled, triggerTitle, triggerIconBusy } =
    useTriggerJob(job);

  return (
    <TableRow
      className="cursor-pointer"
      onClick={() => onOpenHistory(job.name)}
    >
      <TableCell>
        <div className="font-medium">{job.name}</div>
        <div className="text-muted-foreground text-xs">{job.description}</div>
      </TableCell>
      <TableCell>
        <div className="text-sm">{job.schedule}</div>
        <div className="text-muted-foreground font-mono text-[11px]">{job.cron}</div>
      </TableCell>
      <TableCell className="font-mono text-xs">
        {job.enabled ? formatDateTime(job.next_run_at) : (
          <span className="text-muted-foreground">scheduler off</span>
        )}
      </TableCell>
      <TableCell>
        <StatusBadge status={job.last_run?.status ?? null} />
      </TableCell>
      <TableCell className="font-mono text-xs">
        {formatDateTime(job.last_run?.started_at)}
      </TableCell>
      <TableCell className="font-mono text-xs">
        {formatDuration(job.last_run?.duration_s)}
      </TableCell>
      <TableCell onClick={(e) => e.stopPropagation()}>
        <Button
          size="sm"
          variant="outline"
          disabled={triggerDisabled}
          onClick={() => trigger.mutate()}
          title={triggerTitle}
        >
          {triggerIconBusy ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Play className="h-3 w-3" />
          )}
          <span className="ml-1.5">Run now</span>
        </Button>
      </TableCell>
    </TableRow>
  );
}

function JobMobileCard({
  job,
  onOpenHistory,
}: {
  job: JobInfoOut;
  onOpenHistory: (name: string) => void;
}): JSX.Element {
  const { trigger, triggerDisabled, triggerTitle, triggerIconBusy } =
    useTriggerJob(job);

  return (
    <li
      onClick={() => onOpenHistory(job.name)}
      className="active:bg-accent/40 cursor-pointer px-1 py-3 transition-colors"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold tracking-tight">{job.name}</span>
            <StatusBadge status={job.last_run?.status ?? null} />
          </div>
          <div className="text-muted-foreground mt-0.5 line-clamp-2 text-xs">
            {job.description}
          </div>
          <div className="text-muted-foreground mt-1 font-mono text-[11px]">
            {job.schedule} · {job.cron}
          </div>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <div className="bg-muted/30 rounded-md px-2 py-1.5">
          <div className="text-muted-foreground text-[10px] font-medium uppercase tracking-wider">
            Last run
          </div>
          <div className="font-mono text-xs">
            {formatDateTime(job.last_run?.started_at)}
          </div>
        </div>
        <div className="bg-muted/30 rounded-md px-2 py-1.5">
          <div className="text-muted-foreground text-[10px] font-medium uppercase tracking-wider">
            Next run · Duration
          </div>
          <div className="font-mono text-xs">
            {job.enabled ? formatDateTime(job.next_run_at) : "—"} ·{" "}
            {formatDuration(job.last_run?.duration_s)}
          </div>
        </div>
      </div>
      <div
        className="mt-2 flex justify-end"
        onClick={(e) => e.stopPropagation()}
      >
        <Button
          size="sm"
          variant="outline"
          disabled={triggerDisabled}
          onClick={() => trigger.mutate()}
          title={triggerTitle}
        >
          {triggerIconBusy ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Play className="h-3 w-3" />
          )}
          <span className="ml-1.5">Run now</span>
        </Button>
      </div>
    </li>
  );
}

export function Jobs(): JSX.Element {
  const [historyJob, setHistoryJob] = useState<string | null>(null);
  const { data, isLoading, isError } = useQuery({
    queryKey: ["jobs"],
    queryFn: fetchJobs,
    refetchInterval: 10_000,
  });

  return (
    <div className="space-y-8">
      <header className="space-y-1.5">
        <p className="text-primary text-xs font-semibold uppercase tracking-widest">
          System
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">Scheduled jobs</h1>
        <p className="text-muted-foreground max-w-2xl text-sm">
          Background pipelines that keep market data fresh. Click a row to see
          its run history. Schedules are configured via environment variables —
          this view is read-only otherwise.
        </p>
      </header>

      <Card>
        <CardHeader className="px-3 sm:px-5">
          <CardTitle>Registered jobs</CardTitle>
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
            <>
              <ul className="divide-border/50 mx-3 divide-y md:hidden">
                {data.map((job) => (
                  <JobMobileCard
                    key={job.name}
                    job={job}
                    onOpenHistory={setHistoryJob}
                  />
                ))}
              </ul>
              <div className="hidden md:block">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Job</TableHead>
                      <TableHead>Schedule</TableHead>
                      <TableHead>Next run</TableHead>
                      <TableHead>Last status</TableHead>
                      <TableHead>Last started</TableHead>
                      <TableHead>Duration</TableHead>
                      <TableHead></TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.map((job) => (
                      <JobRow
                        key={job.name}
                        job={job}
                        onOpenHistory={setHistoryJob}
                      />
                    ))}
                  </TableBody>
                </Table>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <JobHistoryDialog
        jobName={historyJob}
        open={historyJob !== null}
        onOpenChange={(v) => !v && setHistoryJob(null)}
      />
    </div>
  );
}
