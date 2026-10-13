import { useState } from "react";
import { api, Session } from "./api";

const storageKey = "veilway-mobile-code";
export function pendingMobileApproval() {
  const params = new URLSearchParams(window.location.search);
  const code = new URLSearchParams(window.location.hash.slice(1)).get("code");
  if (params.get("mobile") === "1") {
    sessionStorage.setItem("veilway-mobile-pending", "1");
    if (code && /^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$/.test(code)) sessionStorage.setItem(storageKey, code);
    history.replaceState(null, "", "/?mobile=1");
  }
  return sessionStorage.getItem("veilway-mobile-pending") === "1";
}

export function MobileApproval({ session, onDone }: { session: Session; onDone: () => void }) {
  const [code, setCode] = useState(sessionStorage.getItem(storageKey) ?? "");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");
  async function approve() {
    setBusy(true); setError("");
    try {
      await api.approveMobile(code, session.csrf_token);
      sessionStorage.removeItem(storageKey); sessionStorage.removeItem("veilway-mobile-pending");
      setDone(true);
    } catch { setError("Не удалось подтвердить вход. Проверьте код или начните вход заново в приложении."); }
    finally { setBusy(false); }
  }
  return <main className="login-shell"><section className="login-card">
    <p className="eyebrow">VEILWAY ANDROID</p><h1>{done ? "Вход подтверждён" : "Подключить приложение"}</h1>
    {done ? <p>Вернитесь в приложение Veilway на телефоне.</p> : <>
      <p>Вы вошли как {session.email}. Подтверждайте только вход, который сами начали в приложении Veilway.</p>
      <label>Код должен совпадать с кодом на телефоне<input value={code} maxLength={9} autoComplete="off" spellCheck={false}
        onChange={event => setCode(event.target.value.toUpperCase())} placeholder="ABCD-2345" /></label>
      <p className="muted">Приложение получит доступ к вашим VPN-профилям на 30 дней.</p>
      {error && <p role="alert">{error}</p>}
      <button className="primary" disabled={busy || !/^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$/.test(code)} onClick={approve}>Подтвердить вход</button>
    </>}
    <button className="secondary" disabled={busy} onClick={() => {
      sessionStorage.removeItem(storageKey); sessionStorage.removeItem("veilway-mobile-pending");
      history.replaceState(null, "", "/"); onDone();
    }}>{done ? "В панель" : "Отмена"}</button>
  </section></main>;
}
