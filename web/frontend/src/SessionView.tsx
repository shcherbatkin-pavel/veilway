import { Session } from "./api";
import { Dashboard } from "./Dashboard";
import { AccountHome } from "./AccountHome";

export function SessionView({ session, onLogout }: { session: Session; onLogout: () => void }) {
  return session.role === "ADMIN"
    ? <Dashboard session={session} onLogout={onLogout} />
    : <AccountHome session={session} onLogout={onLogout} />;
}
