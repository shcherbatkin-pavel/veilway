import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, Session } from "./api";
import { Login } from "./Login";
import { Dashboard } from "./Dashboard";
import "./styles.css";

function App() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  useEffect(() => { api.session().then(setSession).catch(() => setSession(null)); }, []);
  if (session === undefined) return <main className="loading">Veilway</main>;
  return session ? <Dashboard session={session} onLogout={() => setSession(null)} /> : <Login onLogin={setSession} />;
}

createRoot(document.getElementById("root")!).render(<App />);
