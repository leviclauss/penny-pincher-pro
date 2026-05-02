import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { LayoutDashboard, ListChecks, Menu, X } from "lucide-react";
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
];

export function AppShell(): JSX.Element {
  const [open, setOpen] = useState(false);

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <header className="border-border bg-card flex items-center justify-between border-b p-3 md:hidden">
        <div className="flex items-center gap-2">
          <span className="text-base font-semibold">Penny Pincher Pro</span>
        </div>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="hover:bg-accent rounded-md p-2"
          aria-label={open ? "Close menu" : "Open menu"}
        >
          {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
      </header>

      <aside
        className={cn(
          "border-border bg-card flex w-full shrink-0 flex-col border-b md:w-56 md:border-b-0 md:border-r",
          open ? "block" : "hidden md:block",
        )}
      >
        <div className="hidden p-4 md:block">
          <div className="text-base font-semibold">Penny Pincher Pro</div>
          <div className="text-muted-foreground text-xs">Wheel options screener</div>
        </div>
        <nav className="flex flex-col p-2">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                )
              }
            >
              <item.icon className="h-4 w-4" />
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <main className="min-w-0 flex-1 p-4 md:p-8">
        <Outlet />
      </main>
    </div>
  );
}
