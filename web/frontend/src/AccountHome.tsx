import { useState } from "react";
import { Session } from "./api";
import { Layout, Section } from "./Layout";
import { Profiles } from "./Profiles";
import { ConnectionGuide } from "./ConnectionGuide";
export function AccountHome({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const [section, setSection] = useState<Section>("profiles");
  return <Layout session={session} section={section} onSection={next => setSection(next === "guide" ? "guide" : "profiles")} onLogout={onLogout}>
    {section === "guide" ? <ConnectionGuide onProfiles={() => setSection("profiles")} /> : <Profiles session={session} onLogout={onLogout} />}
  </Layout>;
}
