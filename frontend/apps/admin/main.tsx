// Quant Desk Admin entry — Phase F7 single-shell mount.
//
// 정책:
//   1) #admin-shell-react-root 발견 시 AdminShell 1회 mount (탭 기반 단일 SPA).
//   2) 발견 못 하면 legacy 4-island multi-mount 로 fallback (이전 phase 호환).
//   3) 두 경우 모두 실패 시 #admin-root (Vite dev) fallback.
//
// 어떤 mount 실패도 다른 mount 를 막지 않는다. Jinja fallback 은 그대로 살아있다.
import '@shared/styles/base.css';
import '@shared/styles/motion.css';
import { StrictMode } from 'react';
import type { ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import AdminShell from './AdminShell';
import AdminRiskEventsIsland from './features/riskEvents/AdminRiskEventsIsland';
import AdminRealtimeIsland from './features/realtime/AdminRealtimeIsland';
import AdminPortfolioIsland from './features/portfolio/AdminPortfolioIsland';

function tryMount(id: string, node: ReactNode): boolean {
  const el = document.getElementById(id);
  if (!el) return false;
  try {
    createRoot(el).render(<StrictMode>{node}</StrictMode>);
    return true;
  } catch (err) {
    console.error(`[admin] React mount failed for #${id}; Jinja fallback remains:`, err);
    return false;
  }
}

const shellMounted = tryMount('admin-shell-react-root', <AdminShell />);

if (!shellMounted) {
  // Legacy multi-island fallback.
  tryMount('admin-overview-react-root',     <App />);
  tryMount('admin-risk-events-react-root',  <AdminRiskEventsIsland />);
  tryMount('admin-realtime-react-root',     <AdminRealtimeIsland />);
  tryMount('admin-portfolio-react-root',    <AdminPortfolioIsland />);
}

if (
  !document.getElementById('admin-shell-react-root') &&
  !document.getElementById('admin-overview-react-root') &&
  !document.getElementById('admin-risk-events-react-root') &&
  !document.getElementById('admin-realtime-react-root') &&
  !document.getElementById('admin-portfolio-react-root')
) {
  tryMount('admin-root', <AdminShell />);
}
