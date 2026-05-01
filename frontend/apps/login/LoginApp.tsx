// Phase F6 — JWT 로그인 페이지.
//
// 동작:
//   1) 비번 입력 → POST /api/auth/login.
//   2) 성공 → JWT 저장 + role 따라 /advanced (admin) / /client (client) 로 이동.
//   3) 실패 → 에러 메시지 표시.
// 안전:
//   - 어디에도 비번을 로그/storage 에 저장하지 않는다.
//   - 토큰만 localStorage 'qd:auth:v1'.
//   - rate limit 은 백엔드 책임 (본 phase 에서 구현 X).

import { useEffect, useState } from 'react';
import { login, AuthError } from '@shared/auth/authClient';
import './login.css';

export default function LoginApp() {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    document.body.classList.add('qd-login-body');
    return () => document.body.classList.remove('qd-login-body');
  }, []);

  async function onSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    if (!password.trim()) {
      setErr('비밀번호를 입력하세요.');
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const auth = await login(password.trim());
      // 라우팅 — admin 은 /advanced, client 는 /client.
      const dest = auth.role === 'admin' ? '/advanced' : '/client';
      window.location.replace(dest);
    } catch (e) {
      const status = e instanceof AuthError ? e.status : 0;
      const msg = e instanceof AuthError ? e.message : '네트워크 오류';
      setErr(status === 401 ? '비밀번호가 틀렸습니다.' : msg);
      setBusy(false);
    }
  }

  return (
    <main className="qd-login-stage" data-app="login" data-phase="F6">
      <section className="qd-login-card qd-scale-in" aria-labelledby="qd-login-title">
        <header className="qd-login-head">
          <div className="qd-login-mark" aria-hidden="true">Q</div>
          <div>
            <h1 id="qd-login-title" className="qd-login-title">Quant Desk</h1>
            <p className="qd-login-sub">대시보드 로그인</p>
          </div>
        </header>

        <form onSubmit={onSubmit} className="qd-login-form" autoComplete="off">
          <label className="qd-login-label" htmlFor="qd-pw">비밀번호</label>
          <input
            id="qd-pw"
            className="qd-login-input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            disabled={busy}
            aria-invalid={!!err}
            aria-describedby={err ? 'qd-login-err' : undefined}
          />
          <button
            type="submit"
            className="qd-login-submit"
            disabled={busy}
          >
            {busy ? <span className="qd-pulse-soft">접속 중…</span> : '로그인'}
          </button>
          {err && (
            <p id="qd-login-err" className="qd-login-err qd-fade-in" role="alert">
              {err}
            </p>
          )}
        </form>

        <footer className="qd-login-foot">
          <span>JWT 인증 · 8h(admin) / 24h(client)</span>
        </footer>
      </section>
    </main>
  );
}
