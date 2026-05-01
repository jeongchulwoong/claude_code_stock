// Phase F3 — Admin Overview Island 진입점.
import AdminOverviewIsland from './features/overview/AdminOverviewIsland';
import type { UseOverviewPollingOptions } from './features/overview/useOverviewPolling';

export interface AppProps {
  overviewPollingOptions?: UseOverviewPollingOptions;
}

export default function App(props: AppProps = {}) {
  return (
    <main className="qd-admin-island" data-phase="F3" data-app="admin">
      <AdminOverviewIsland pollingOptions={props.overviewPollingOptions} />
    </main>
  );
}
