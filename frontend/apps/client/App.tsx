// Phase F5 — Client App.
import ClientScreenerIsland, {
  type ClientScreenerIslandProps,
} from './features/screener/ClientScreenerIsland';

export interface AppProps {
  islandProps?: ClientScreenerIslandProps;
}

export default function App(props: AppProps = {}) {
  return (
    <main className="qd-client-app" data-app="client" data-phase="F5" data-island="client-screener">
      <ClientScreenerIsland {...(props.islandProps ?? {})} />
    </main>
  );
}
