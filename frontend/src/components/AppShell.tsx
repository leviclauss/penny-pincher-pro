import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Activity, Clock, LayoutDashboard, ListChecks, Menu, Sparkles, X } from "lucide-react";
import { fetchHealth } from "@/api/client";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

interface NavItem {
  to: string;
  label: string;
  icon: typeof LayoutDashboard;
  end?: boolean;
}

const NAV: NavItem[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/tickers", label: "Tickers", icon: ListChecks },
  { to: "/jobs", label: "Jobs", icon: Clock },
];

function BrandMark(): JSX.Element {
  return (
    <div className="flex items-center gap-2">
      <div className="from-primary relative flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br to-fuchsia-500 shadow-md">
        <Sparkles className="text-primary-foreground h-4 w-4" />
      </div>
      <div className="leading-tight">
        <div className="text-sm font-semibold tracking-tight">Penny Pincher Pro</div>
        <div className="text-muted-foreground text-[10px] uppercase tracking-widest">
          Wheel screener
        </div>
      </div>
    </div>
  );
}

function HealthDot(): JSX.Element {
  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 30_000,
  });
  const ok = data?.status === "ok" && !isError;
  return (
    <div className="border-border/60 bg-card/60 text-muted-foreground flex items-center gap-2 rounded-full border px-3 py-1 text-xs">
      <span className="relative flex h-2 w-2">
        <span
          className={cn(
            "absolute inline-flex h-full w-full animate-ping rounded-full opacity-60",
            ok ? "bg-emerald-400" : "bg-red-500",
          )}
        />
        <span
          className={cn(
            "relative inline-flex h-2 w-2 rounded-full",
            ok ? "bg-emerald-400" : "bg-red-500",
          )}
        />
      </span>
      <Activity className="h-3 w-3" />
      <span className="font-mono">
        {data?.last_bar_date ? formatDate(data.last_bar_date) : isError ? "offline" : "—"}
      </span>
    </div>
  );
}

export function AppShell(): JSX.Element {
  const [open, setOpen] = useState(false);

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <header className="bg-sidebar/80 supports-[backdrop-filter]:bg-sidebar/60 sticky top-0 z-40 flex items-center justify-between border-b border-[hsl(var(--sidebar-border))] px-4 py-3 backdrop-blur md:hidden">
        <BrandMark />
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="hover:bg-accent rounded-md p-2 transition-colors"
          aria-label={open ? "Close menu" : "Open menu"}
        >
          {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
      </header>

      <aside
        className={cn(
          "bg-sidebar/80 supports-[backdrop-filter]:bg-sidebar/60 flex w-full shrink-0 flex-col border-b border-[hsl(var(--sidebar-border))] backdrop-blur md:sticky md:top-0 md:h-screen md:w-60 md:border-b-0 md:border-r",
          open ? "block" : "hidden md:flex",
        )}
      >
        <div className="hidden px-5 py-5 md:block">
          <BrandMark />
        </div>
        <nav className="flex flex-col gap-0.5 px-3 pb-3">
          <div className="text-muted-foreground/70 px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-widest">
            Workspace
          </div>
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                cn(
                  "group flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-accent text-accent-foreground shadow-sm"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                )
              }
            >
              <item.icon className="h-4 w-4 opacity-80 group-hover:opacity-100" />
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto hidden px-5 py-4 md:block">
          <HealthDot />
        </div>
      </aside>

      <main className="min-w-0 flex-1">
        <div className="border-border/40 bg-background/40 supports-[backdrop-filter]:bg-background/30 sticky top-0 z-30 hidden items-center justify-between border-b px-8 py-3 backdrop-blur md:flex">
          <div className="text-muted-foreground text-xs uppercase tracking-widest">
            Daily review
          </div>
          <HealthDot />
        </div>
        <div className="px-4 py-6 md:px-8 md:py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
