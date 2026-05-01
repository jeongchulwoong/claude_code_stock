// Phase F5 — Client entry. #client-react-root 만 mount.
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';

const container = document.getElementById('client-react-root');
if (container) {
  try {
    createRoot(container).render(
      <StrictMode>
        <App />
      </StrictMode>,
    );
  } catch (err) {
    console.error('[client] React mount failed; Jinja fallback remains:', err);
  }
}
