import { FormEvent, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, ApiError, RestartJob, Session, VpnVm, VmSlug } from "./api";
import "./styles.css";

const stateLabels: Record<VpnVm["state"], string> = {
  healthy: "Работает",
  degraded: "Требует внимания",
  unknown: "Нет данных",
  restarting: "Перезагружается",
};

function Login({ onLogin }: { onLogin: (session: Session) => void }) {
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

function Dashboard({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const [vms, setVms] = useState<VpnVm[]>([]);
  const [jobs, setJobs] = useState<RestartJob[]>([]);
  const [selected, setSelected] = useState<Set<VmSlug>>(new Set());
  const [confirming, setConfirming] = useState<VmSlug[] | null>(null);
  const [error, setError] = useState("");

  async function refresh() {
    try {
      const [nextVms, nextJobs] = await Promise.all([api.vms(), api.jobs()]);
      setVms(nextVms);
      setJobs(nextJobs);
      setError("");
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onLogout();
      else setError("Не удалось обновить состояние");
    }
  }

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, []);

  const active = useMemo(() => jobs.some((job) => ["queued", "dispatching", "waiting"].includes(job.status)), [jobs]);
  const healthySlugs = useMemo(() => new Set(vms.filter((vm) => vm.state === "healthy").map((vm) => vm.slug)), [vms]);
  const selectedAreHealthy = [...selected].every((slug) => healthySlugs.has(slug));
  const allHealthy = vms.length === 2 && vms.every((vm) => vm.state === "healthy");

  function toggle(slug: VmSlug) {
    setSelected((current) => {
      const next = new Set(current);
      next.has(slug) ? next.delete(slug) : next.add(slug);
      return next;
    });
  }

  async function restart(targets: VmSlug[]) {
    try {
      await api.createJob(targets, session.csrf_token);
      setSelected(new Set());
      setConfirming(null);
      await refresh();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Не удалось создать операцию");
    }
  }

  async function logout() {
    try { await api.logout(session.csrf_token); } finally { onLogout(); }
  }

  return (
    <main className="dashboard">
      <header><div><p className="eyebrow">VEILWAY.RU</p><h1>VPN infrastructure</h1></div><div className="account"><span>{session.login}</span><button className="ghost" onClick={() => void logout()}>Выйти</button></div></header>
      {error && <div className="banner error">{error}</div>}
      <section className="summary"><div><span>Узлы</span><strong>{vms.length}</strong></div><div><span>Healthy</span><strong>{vms.filter((vm) => vm.state === "healthy").length}</strong></div><div><span>Операция</span><strong>{active ? "Активна" : "Нет"}</strong></div></section>
      <section className="section-heading"><div><p className="eyebrow">LIVE STATUS</p><h2>VPN-узлы</h2></div><div className="actions"><button className="secondary" disabled={active || selected.size === 0 || !selectedAreHealthy} onClick={() => setConfirming([...selected])}>Перезагрузить выбранные</button><button className="danger" disabled={active || !allHealthy} onClick={() => setConfirming(["aws-direct", "yc-direct"])}>Перезагрузить все</button></div></section>
      <section className="vm-grid">{vms.map((vm) => <article className={`vm-card ${vm.state}`} key={vm.slug}><div className="vm-title"><label className="check"><input type="checkbox" checked={selected.has(vm.slug)} disabled={active || vm.state !== "healthy"} onChange={() => toggle(vm.slug)} /><span /></label><div><h3>{vm.slug}</h3><p>{vm.provider === "aws" ? "Amazon Web Services" : "Yandex Cloud"}</p></div><span className="status-pill">{stateLabels[vm.state]}</span></div><div className="heartbeat"><span>Последний heartbeat</span><strong>{vm.last_heartbeat_at ? new Date(vm.last_heartbeat_at).toLocaleString("ru-RU") : "—"}</strong></div></article>)}</section>
      <section className="history"><div className="section-heading"><div><p className="eyebrow">OPERATIONS</p><h2>История</h2></div></div>{jobs.length === 0 ? <p className="empty">Операций пока нет</p> : jobs.map((job) => <article className="job" key={job.id}><div><strong>{job.targets.map((target) => target.slug).join(" → ")}</strong><span>{new Date(job.created_at).toLocaleString("ru-RU")}</span></div><span className={`job-status ${job.status}`}>{job.status}</span></article>)}</section>
      {confirming && <div className="modal-backdrop" role="presentation"><section className="modal" role="dialog" aria-modal="true"><p className="eyebrow">ПОДТВЕРЖДЕНИЕ</p><h2>Перезагрузить {confirming.length === 2 ? "все VPN-узлы" : confirming[0]}?</h2><p>Активные VPN-соединения будут временно разорваны. При массовой операции AWS будет восстановлен до перезагрузки Yandex.</p><div className="modal-actions"><button className="ghost" onClick={() => setConfirming(null)}>Отмена</button><button className="danger" onClick={() => void restart(confirming)}>Перезагрузить</button></div></section></div>}
    </main>
  );
}

function App() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  useEffect(() => { api.session().then(setSession).catch(() => setSession(null)); }, []);
  if (session === undefined) return <main className="loading">Veilway</main>;
  return session ? <Dashboard session={session} onLogout={() => setSession(null)} /> : <Login onLogin={setSession} />;
}

createRoot(document.getElementById("root")!).render(<App />);
