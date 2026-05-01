// Quant Desk Admin entry — Phase F3 + F4 multi-mount.
//
// 마운트 대상 (각각 독립 try/catch):
//   1) #admin-overview-react-root      (F3 — 운영 위험 요약)
//   2) #admin-risk-events-react-root   (F4 — 위험 이벤트)
//   3) #admin-realtime-react-root      (F4 — 실시간 4-section)
//   4) #admin-root                     (Vite dev 단독 페이지 fallback)
import { StrictMode } from 'react';
import type { ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import AdminRiskEventsIsland from './features/riskEvents/AdminRiskEventsIsland';
import AdminRealtimeIsland from './features/realtime/AdminRealtimeIsland';

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

tryMount('admin-overview-react-root',     <App />);
tryMount('admin-risk-events-react-root',  <AdminRiskEventsIsland />);
tryMount('admin-realtime-react-root',     <AdminRealtimeIsland />);

if (
  !document.getElementById('admin-overview-react-root') &&
  !document.getElementById('admin-risk-events-react-root') &&
  !document.getElementById('admin-realtime-react-root')
) {
  tryMount('admin-root', <App />);
}
