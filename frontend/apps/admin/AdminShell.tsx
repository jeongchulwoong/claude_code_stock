// Phase F7 — AdminShell: single React root, tabbed admin console.
//
// 정책:
//   - 하나의 mount root (#admin-shell-react-root) 만 사용. 기존 multi-island 는 fallback.
//   - 탭 전환은 client-side. 서버 라우트 추가 0건. URL hash 로 deep-link.
//   - 각 탭은 기존 island 컴포넌트 재사용. polling 은 현재 탭만 활성 (성능).
//   - 매수/매도/주문 트리거 0건. 모든 탭 read-only.

import { useEffect, useState } from 'react';
import AdminOverviewIsland from './features/overview/AdminOverviewIsland';
import AdminPortfolioIsland from './features/portfolio/AdminPortfolioIsland';
import AdminRiskEventsIsland from './features/riskEvents/AdminRiskEventsIsland';
import AdminRealtimeIsland from './features/realtime/AdminRealtimeIsland';
import OrdersTab from './features/orders/OrdersTab';
import DaytradeTab from './features/daytrade/DaytradeTab';
import ScreenerTab from './features/screener/ScreenerTab';
import SettingsTab from './features/settings/SettingsTab';
import WatchlistTab from './features/watchlist/WatchlistTab';
import StrategiesTab from './features/strategies/StrategiesTab';
import ChartTab from './features/chart/ChartTab';
import { logout } from '@shared/auth/authClient';
import { loadAuth } from '@shared/auth/tokenStorage';
import './admin-shell.css';

export type AdminTabId =
  | 'overview' | 'portfolio' | 'daytrade'
  | 'realtime' | 'risk' | 'orders'
  | 'screener' | 'watchlist' | 'strategies' | 'chart' | 'settings';

interface TabDef {
  id: AdminTabId;
  label: string;
  Component: () => JSX.Element;
}

const TABS: TabDef[] = [
  { id: 'overview',   label: 'Overview',   Component: () => <AdminOverviewIsland /> },
  { id: 'portfolio',  label: 'Portfolio',  Component: () => <AdminPortfolioIsland /> },
  { id: 'daytrade',   label: '분단타',     Component: DaytradeTab },
  { id: 'realtime',   label: 'Realtime',   Component: () => <AdminRealtimeIsland /> },
  { id: 'risk',       label: 'Risk',       Component: () => <AdminRiskEventsIsland /> },
  { id: 'orders',     label: 'Orders',     Component: OrdersTab },
  { id: 'screener',   label: 'Screener',   Component: ScreenerTab },
  { id: 'watchlist',  label: 'Watchlist',  Component: WatchlistTab },
  { id: 'strategies', label: 'Strategies', Component: StrategiesTab },
  { id: 'chart',      label: 'Chart',      Component: ChartTab },
  { id: 'settings',   label: 'Settings',   Component: SettingsTab },
];

const TAB_IDS = new Set<AdminTabId>(TABS.map((t) => t.id));

function readHashTab(): AdminTabId {
  const raw = (window.location.hash || '').replace(/^#/, '').trim().toLowerCase();
  return TAB_IDS.has(raw as AdminTabId) ? (raw as AdminTabId) : 'overview';
}

export default function AdminShell() {
  const [active, setActive] = useState<AdminTabId>(() => readHashTab());
  const [auth, setAuth] = useState(() => loadAuth());

  useEffect(() => {
    const onHash = () => setActive(readHashTab());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  function selectTab(id: AdminTabId) {
    if (id === active) return;
    window.location.hash = id;
    setActive(id);
  }

  async function onLogout() {
    await logout();
    setAuth(null);
    window.location.replace('/login');
  }

  const ActiveComponent = (TABS.find((t) => t.id === active) ?? TABS[0]!).Component;

  return (
    <main className="qd-shell" data-app="admin" data-phase="F7" data-active-tab={active}>
      <header className="qd-shell-bar">
        <div className="qd-shell-brand">
          <span className="qd-shell-mark" aria-hidden="true">QD</span>
          <span className="qd-shell-title">Quant Desk</span>
          <span className="qd-shell-sub">Admin Console</span>
        </div>
        <nav className="qd-shell-tabs" role="tablist" aria-label="Admin sections">
          {TABS.map((t) => {
            const isActive = t.id === active;
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={isActive}
                data-tab={t.id}
                className={`qd-shell-tab${isActive ? ' is-active' : ''}`}
                onClick={() => selectTab(t.id)}
              >
                {t.label}
              </button>
            );
          })}
        </nav>
        <div className="qd-shell-actions">
          {auth && (
            <span className="qd-shell-role qd-num" data-role={auth.role}>
              {auth.role === 'admin' ? 'admin' : 'client'}
            </span>
          )}
          <button
            type="button"
            className="qd-shell-logout"
            onClick={onLogout}
            aria-label="로그아웃"
          >
            로그아웃
          </button>
        </div>
      </header>

      <section
        className="qd-shell-panel qd-fade-in"
        role="tabpanel"
        aria-labelledby={`tab-${active}`}
        key={active}
      >
        <ActiveComponent />
      </section>
    </main>
  );
}
