import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, ApiError, errorMessage, Session } from "./api";
import { Login } from "./Login";
import { SessionView } from "./SessionView";
import "./styles.css";
function App() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setError("");
    api.session().then(value => { if (active) setSession(value); }).catch(reason => {
      if (!active) return;
      if (reason instanceof ApiError && reason.status === 401) setSession(null);
      else setError(errorMessage(reason));
    });
    return () => { active = false; };
  }, [attempt]);
  if (session === undefined) return error ? <main className="loading"><div className="session-error"><p role="alert">{error}</p><button className="primary" onClick={() => setAttempt(value => value + 1)}>Повторить</button></div></main> : <main className="loading" role="status">Загружаем Veilway…</main>;
  return session ? <SessionView key={session.user_id} session={session} onLogout={() => { setSession(null); setMessage("Сессия завершена. Войдите через Google снова."); }} /> : <Login message={message} errorCode={new URLSearchParams(window.location.search).get("auth_error")} />;
}
createRoot(document.getElementById("root")!).render(<App />);
