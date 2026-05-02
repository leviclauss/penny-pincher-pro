import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { CalendarDays } from "lucide-react";
import { fetchUpcomingEarnings } from "@/api/client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/Table";

export function UpcomingEarnings(): JSX.Element {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["earnings", "upcoming", 7],
    queryFn: () => fetchUpcomingEarnings(7),
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Upcoming earnings</CardTitle>
          <span className="border-border/60 bg-background/40 text-muted-foreground inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px]">
            <CalendarDays className="h-3 w-3" />
            Next 7 days
          </span>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading && <div className="text-muted-foreground text-sm">Loading…</div>}
        {isError && <div className="text-destructive text-sm">Failed to load earnings.</div>}
        {data && data.length === 0 && (
          <div className="text-muted-foreground text-sm">
            No earnings in the next 7 days.
          </div>
        )}
        {data && data.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Symbol</TableHead>
                <TableHead>Name</TableHead>
                <TableHead>When</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((row) => (
                <TableRow key={`${row.symbol}-${row.earnings_date}`}>
                  <TableCell className="text-muted-foreground font-mono text-xs">
                    {row.earnings_date}
                  </TableCell>
                  <TableCell>
                    <Link
                      to={`/tickers/${row.symbol}`}
                      className="text-foreground hover:text-primary font-semibold transition-colors"
                    >
                      {row.symbol}
                    </Link>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{row.name ?? "—"}</TableCell>
                  <TableCell>
                    {row.time_of_day ? (
                      <span className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider">
                        {row.time_of_day}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
