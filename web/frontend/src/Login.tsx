export function Login({ errorCode, message }: { errorCode?: string | null; message?: string }) {
  const error = errorCode === "cancelled"
    ? "Вход отменён. Можно попробовать ещё раз."
    : errorCode === "unavailable"
      ? "Вход временно недоступен. Попробуйте позже."
      : errorCode ? "Не удалось выполнить вход. Попробуйте ещё раз." : "";

  return (
    <main className="login-shell">
      <section className="login-card">
        <div className="brand-mark">V</div>
        <p className="eyebrow">VEILWAY</p>
        <h1>Ваш VPN</h1>
        <p className="muted">Войдите через Google, чтобы получить доступ к своим VPN-профилям.</p>
        {message && <p className="muted" role="status">{message}</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <a className="primary google-login" href="/api/v1/auth/google/start">Войти через Google</a>
        <p className="login-note muted">При первом входе аккаунт создаётся автоматически. Доступ к VPN выдаёт администратор.</p>
      </section>
    </main>
  );
}
