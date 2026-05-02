import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
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
        <CardTitle>Upcoming earnings</CardTitle>
        <p className="text-muted-foreground text-xs">Next 7 days, active watchlist only</p>
      </CardHeader>
      <CardContent>
        {isLoading && <div className="text-muted-foreground text-sm">Loading…</div>}
        {isError && <div className="text-destructive text-sm">Failed to load earnings.</div>}
        {data && data.length === 0 && (
          <div className="text-muted-foreground text-sm">No earnings in the next 7 days.</div>
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
                  <TableCell className="font-mono text-xs">{row.earnings_date}</TableCell>
                  <TableCell className="font-semibold">
                    <Link to={`/tickers/${row.symbol}`} className="hover:underline">
                      {row.symbol}
                    </Link>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{row.name ?? "—"}</TableCell>
                  <TableCell className="text-muted-foreground text-xs uppercase">
                    {row.time_of_day ?? "—"}
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
