import { useState } from "react";
import { Session } from "./api";
import { Layout, Section } from "./Layout";
import { Profiles } from "./Profiles";
import { Users } from "./Users";
import { Nodes } from "./Nodes";
import { Health } from "./Health";
import { History } from "./History";
import { ConnectionGuide } from "./ConnectionGuide";
export function Dashboard({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const [section, setSection] = useState<Section>("profiles");
  const [owner, setOwner] = useState<string>();
  return <Layout session={session} section={section} onLogout={onLogout} onSection={next => { setOwner(undefined); setSection(next); }}>
    {section === "health" && <Health onLogout={onLogout} onSection={setSection} />}
    {section === "profiles" && <Profiles key={owner ?? "all"} session={session} onLogout={onLogout} onlyOwner={owner} />}
    {section === "users" && <Users onLogout={onLogout} onProfiles={id => { setOwner(id); setSection("profiles"); }} />}
    {section === "nodes" && <Nodes session={session} onLogout={onLogout} />}
    {section === "history" && <History onLogout={onLogout} />}
    {section === "guide" && <ConnectionGuide onProfiles={() => { setOwner(undefined); setSection("profiles"); }} />}
  </Layout>;
}
