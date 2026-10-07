import { useState } from "react";
import { Session } from "./api";
import { Layout, Section } from "./Layout";
import { Profiles } from "./Profiles";
import { Users } from "./Users";
import { Nodes } from "./Nodes";
import { History } from "./History";
export function Dashboard({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const [section, setSection] = useState<Section>("profiles");
  const [owner, setOwner] = useState<string>();
  return <Layout session={session} section={section} onLogout={onLogout} onSection={next => { setOwner(undefined); setSection(next); }}>
    {section === "profiles" && <Profiles key={owner ?? "all"} session={session} onLogout={onLogout} onlyOwner={owner} />}
    {section === "users" && <Users onLogout={onLogout} onProfiles={id => { setOwner(id); setSection("profiles"); }} />}
    {section === "nodes" && <Nodes session={session} onLogout={onLogout} />}
    {section === "history" && <History onLogout={onLogout} />}
  </Layout>;
}
