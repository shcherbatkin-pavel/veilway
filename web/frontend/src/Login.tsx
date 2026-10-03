import { FormEvent, useState } from "react";
import { api, ApiError, Session } from "./api";

export function Login({ onLogin }: { onLogin: (session: Session) => void }) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      onLogin(await api.login(login, password));
    } catch (reason) {
      setError(reason instanceof ApiError && reason.status === 401 ? "Неверный логин или пароль" : "Сервис временно недоступен");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-card">
        <div className="brand-mark">V</div>
        <p className="eyebrow">VEILWAY CONTROL</p>
        <h1>Управление VPN</h1>
        <p className="muted">Закрытая панель обслуживания узлов</p>
        <form onSubmit={submit}>
          <label>Логин<input autoComplete="username" value={login} onChange={(e) => setLogin(e.target.value)} required /></label>
          <label>Пароль<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
          {error && <p className="error">{error}</p>}
          <button className="primary" disabled={busy}>{busy ? "Проверяем…" : "Войти"}</button>
        </form>
      </section>
    </main>
  );
}
