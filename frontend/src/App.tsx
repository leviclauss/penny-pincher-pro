import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { Dashboard } from "@/pages/Dashboard";
import { Jobs } from "@/pages/Jobs";
import { NotFound } from "@/pages/NotFound";
import { Screener } from "@/pages/Screener";
import { TickerDetail } from "@/pages/TickerDetail";
import { Tickers } from "@/pages/Tickers";

export function App(): JSX.Element {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Dashboard />} />
          <Route path="tickers" element={<Tickers />} />
          <Route path="tickers/:symbol" element={<TickerDetail />} />
          <Route path="screener" element={<Screener />} />
          <Route path="jobs" element={<Jobs />} />
          <Route path="404" element={<NotFound />} />
          <Route path="*" element={<Navigate to="/404" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
