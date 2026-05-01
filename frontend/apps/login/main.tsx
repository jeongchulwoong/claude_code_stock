// Phase F6 — Login entry. Jinja login.html 의 #login-react-root 에 mount.
import '@shared/styles/base.css';
import '@shared/styles/motion.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import LoginApp from './LoginApp';

const container = document.getElementById('login-react-root');
if (container) {
  try {
    createRoot(container).render(
      <StrictMode>
        <LoginApp />
      </StrictMode>,
    );
  } catch (err) {
    console.error('[login] React mount failed; Jinja fallback remains:', err);
  }
}
