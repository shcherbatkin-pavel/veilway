import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, Session, VmSlug } from "./api";
import { useDashboardPolling } from "./useDashboardPolling";
import { VmCard } from "./VmCard";
import { JobHistory } from "./JobHistory";
import { RestartConfirmation } from "./RestartConfirmation";

export function Dashboard({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const { vms, jobs, error: pollingError, refresh } = useDashboardPolling(onLogout);
  const [selected, setSelected] = useState<Set<VmSlug>>(new Set());
  const [confirming, setConfirming] = useState<VmSlug[] | null>(null);
  const [error, setError] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const inFlight = useRef(false);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
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
    if (inFlight.current) return;
    inFlight.current = true;
    setSubmitting(true);
    setError("");
    try {
      await api.createJob(targets, session.csrf_token);
      if (!mounted.current) return;
      setSelected(new Set());
      setConfirming(null);
      await refresh();
    } catch (reason) {
      if (!mounted.current) return;
      if (reason instanceof ApiError && reason.status === 401) onLogout();
      else setError(reason instanceof ApiError ? reason.message : "Не удалось создать операцию");
    } finally {
      inFlight.current = false;
      if (mounted.current) setSubmitting(false);
    }
  }

  async function logout() {
    try { await api.logout(session.csrf_token); } finally { onLogout(); }
  }

  return (
    <main className="dashboard">
      <header><div><p className="eyebrow">VEILWAY.RU</p><h1>VPN infrastructure</h1></div><div className="account"><span>{session.login}</span><button className="ghost" onClick={() => void logout()}>Выйти</button></div></header>
      {(error || pollingError) && <div className="banner error">{error || pollingError}</div>}
      <section className="summary"><div><span>Узлы</span><strong>{vms.length}</strong></div><div><span>Healthy</span><strong>{vms.filter((vm) => vm.state === "healthy").length}</strong></div><div><span>Операция</span><strong>{active ? "Активна" : "Нет"}</strong></div></section>
      <section className="section-heading"><div><p className="eyebrow">LIVE STATUS</p><h2>VPN-узлы</h2></div><div className="actions"><button className="secondary" disabled={active || selected.size === 0 || !selectedAreHealthy} onClick={() => setConfirming([...selected])}>Перезагрузить выбранные</button><button className="danger" disabled={active || !allHealthy} onClick={() => setConfirming(["aws-direct", "yc-direct"])}>Перезагрузить все</button></div></section>
      <section className="vm-grid">{vms.map((vm) => <VmCard key={vm.slug} vm={vm} selected={selected.has(vm.slug)} disabled={active} onToggle={() => toggle(vm.slug)} />)}</section>
      <JobHistory jobs={jobs} />
      {confirming && <RestartConfirmation targets={confirming} busy={submitting} onCancel={() => setConfirming(null)} onConfirm={() => void restart(confirming)} />}
    </main>
  );
}
