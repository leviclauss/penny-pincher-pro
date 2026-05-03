import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { Alerts } from "@/pages/Alerts";
import { Dashboard } from "@/pages/Dashboard";
import { Jobs } from "@/pages/Jobs";
import { NotFound } from "@/pages/NotFound";
import { PositionDetail } from "@/pages/PositionDetail";
import { Positions } from "@/pages/Positions";
import { Screener } from "@/pages/Screener";
import { ScreenerConfigEditor } from "@/pages/ScreenerConfigEditor";
import { ScreenerConfigs } from "@/pages/ScreenerConfigs";
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
          <Route path="screener/configs" element={<ScreenerConfigs />} />
          <Route path="screener/configs/new" element={<ScreenerConfigEditor />} />
          <Route path="screener/configs/:id" element={<ScreenerConfigEditor />} />
          <Route path="positions" element={<Positions />} />
          <Route path="positions/:id" element={<PositionDetail />} />
          <Route path="alerts" element={<Alerts />} />
          <Route path="jobs" element={<Jobs />} />
          <Route path="404" element={<NotFound />} />
          <Route path="*" element={<Navigate to="/404" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
