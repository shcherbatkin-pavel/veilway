import { Session } from "./api";
import { Layout } from "./Layout";
import { Profiles } from "./Profiles";
export function AccountHome({ session, onLogout }: { session: Session; onLogout: () => void }) {
  return <Layout session={session} onLogout={onLogout}><Profiles session={session} onLogout={onLogout} /></Layout>;
}
