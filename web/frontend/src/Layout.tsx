import { ReactNode, useState } from "react";
import { api, errorMessage, Session } from "./api";

export type Section = "profiles" | "users" | "nodes" | "history" | "guide" | "health";
export const sections: { id: Section; label: string; icon: string }[] = [
  { id: "health", label: "Состояние", icon: "◎" },
  { id: "profiles", label: "Профили", icon: "▤" }, { id: "users", label: "Пользователи", icon: "◉" },
  { id: "nodes", label: "Узлы", icon: "◇" }, { id: "history", label: "История", icon: "◷" },
  { id: "guide", label: "Как подключиться", icon: "?" },
];
export function Layout({ session, section = "profiles", onSection, onLogout, children }: {
  session: Session; section?: Section; onSection?: (section: Section) => void; onLogout: () => void; children: ReactNode;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigation = session.role === "ADMIN" ? sections : sections
    .filter(item => item.id === "profiles" || item.id === "guide")
    .map(item => item.id === "profiles" ? { ...item, label: "Мои профили" } : item);
  async function logout() {
    if (busy) return;
    setBusy(true);
    try { await api.logout(session.csrf_token); onLogout(); }
    catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  }
  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#" aria-label="Veilway — главная" onClick={event => { event.preventDefault(); onSection?.("profiles"); }}>
        <svg viewBox="0 0 32 32" aria-hidden="true"><path d="M5 5h8l3 14L22 5h7L18 28h-7z" fill="currentColor" /></svg><span>veilway<span className="brand-dot">.</span></span>
      </a>
      <p className="nav-caption">{session.role === "ADMIN" ? "УПРАВЛЕНИЕ" : "ЛИЧНЫЙ КАБИНЕТ"}</p>
      <nav aria-label="Основная навигация">
        {navigation.map(item => <button key={item.id} className={`nav-link ${section === item.id ? "selected" : ""}`} aria-current={section === item.id ? "page" : undefined} onClick={() => onSection?.(item.id)}><span aria-hidden="true">{item.icon}</span>{item.label}</button>)}
      </nav>
      <div className="sidebar-note"><span className="live-dot" />{session.role === "ADMIN" ? "Панель управления VPN" : "Ваш доступ к VPN"}</div>
      <div className="account"><div className="avatar" aria-hidden="true">{session.email.slice(0, 1).toUpperCase()}</div><div className="identity"><strong title={session.email}>{session.email}</strong><span>{session.role === "ADMIN" ? "Администратор" : "Пользователь"}</span></div><button className="icon-button" aria-label="Выйти" disabled={busy} onClick={() => void logout()}>↪</button></div>
    </aside>
    <main className="workspace">{error && <div role="alert" className="banner error">{error}</div>}{children}<footer>VEILWAY <span>Личный доступ. Под вашим контролем.</span></footer></main>
  </div>;
}
