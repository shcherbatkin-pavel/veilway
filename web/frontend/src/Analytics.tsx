import { useMemo, useRef, useState } from "react";
import type { ProfileAnalytics, ProfileMode } from "./api";
import { AnalyticsView, AnalyticsSnapshot, buildAnalyticsRequest, loadAnalytics } from "./analyticsResource";
import { useResource } from "./useResource";
import type { ChartMetric } from "./analyticsChart";
import { modeNames, sourceNames, date, number } from "./analyticsFormatting";
import { OverviewView, ActivityView, ProfilesView, SessionsView, SelectedAnalyticsProfile } from "./AnalyticsViews";

const tabs: { id: AnalyticsView; label: string }[] = [{ id: "overview", label: "Обзор" }, { id: "activity", label: "Активность" }, { id: "profiles", label: "Профили" }, { id: "sessions", label: "Сессии" }];
export function Analytics({ onLogout }: { onLogout: () => void }) {
  const [view, setView] = useState<AnalyticsView>("overview");
  const [days, setDays] = useState(30);
  const [mode, setMode] = useState<ProfileMode | "">("");
  const [onlineOnly, setOnlineOnly] = useState(false);
  const [profileOffset, setProfileOffset] = useState(0);
  const [sessionOffset, setSessionOffset] = useState(0);
  const [selected, setSelected] = useState<SelectedAnalyticsProfile | null>(null);
  const [chartMetric, setChartMetric] = useState<ChartMetric>("users");
  const [failureKey, setFailureKey] = useState("");
  const tabButtons = useRef<(HTMLButtonElement | null)[]>([]);
  const { key, overviewQuery, detailQuery } = buildAnalyticsRequest({ view, days, mode, onlineOnly, profileOffset, sessionOffset, selected });
  const load = useMemo(() => async (signal: AbortSignal): Promise<AnalyticsSnapshot> => {
    try {
      return await loadAnalytics({ view, key, overviewQuery, detailQuery }, signal);
    } catch (reason) {
      if (!signal.aborted) setFailureKey(key);
      throw reason;
    }
  }, [view, key, overviewQuery, detailQuery]);
  const resource = useResource(load, onLogout);
  const snapshot = resource.data?.key === key ? resource.data : undefined;
  const data = snapshot?.overview;
  const error = failureKey === key ? resource.error : "";
  function reset() { setProfileOffset(0); setSessionOffset(0); setSelected(null); }
  function openSessions(profile: ProfileAnalytics, from: "overview" | "profiles") { setSelected({ id: profile.profile_id, name: profile.device_name, from }); setSessionOffset(0); setView("sessions"); }
  return <div className="analytics-page">
    <header className="page-heading"><div><p className="eyebrow">ИСПОЛЬЗОВАНИЕ VPN</p><h1>Аналитика</h1><p className="muted">Подключения сейчас и использование VPN.</p></div><button className="ghost" onClick={() => void resource.refresh()}>Обновить</button></header>
    <div className="analytics-toolbar"><label>Режим<select aria-label="Режим аналитики" value={mode} onChange={e => { setMode(e.target.value as typeof mode); reset(); }}><option value="">Все режимы</option>{Object.entries(modeNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      {view !== "overview" && <label>Период<select aria-label="Период аналитики" value={days} onChange={e => { setDays(Number(e.target.value)); reset(); }}>{[7, 30, 90, 365].map(d => <option key={d} value={d}>{d} дней</option>)}</select></label>}
      <span className="analytics-freshness muted" role="status">{data ? `${error ? "Снимок устарел" : data.online.complete ? "Свежие данные" : "Данные неполные"} · обновлено ${date(data.generated_at)}` : "Ожидаем свежие данные"}<small>Обновление каждые 5 секунд</small></span>
    </div>
    {data && <details className="analytics-sources"><summary>Источники <span className="muted">{data.sources.filter(s => s.state === "fresh").length} из {data.sources.length} доступны</span></summary><div className="analytics-source-list">{data.sources.map(source => <div key={source.mode}><strong>{modeNames[source.mode]}</strong><span>{sourceNames[source.state]}</span><small className="muted">Наблюдение: {date(source.observed_at)} · соединения: {number(source.connections)} · пропуски: {number(source.capture_errors)}</small></div>)}</div><p className="muted">Сбор наблюдений: {data.collection_started_at ? date(data.collection_started_at) : "ещё не начат"}. Без сопоставленного профиля: {data.online.unmatched_connections}. Профилей без известного владельца: {data.online.unassigned_profiles}.</p></details>}
    {error && <div role="alert" className="banner error">{error} {snapshot ? "Показан устаревший снимок." : "Свежие данные недоступны."}</div>}
    {data && !data.online.complete && <div role="status" className="banner">Онлайн-данные неполные. Подтверждено соединений: {number(data.online.confirmed_connections)}. Недоступные источники не считаются пустыми.</div>}
    {data?.sources.some(s => s.capture_errors > 0) && <p role="alert" className="banner error">Зарегистрированы пропуски сбора. История сессий может быть неполной.</p>}
    <div className="analytics-tabs" role="tablist" aria-label="Разделы аналитики">{tabs.map((tab, i) => <button key={tab.id} ref={button => { tabButtons.current[i] = button; }} id={`analytics-tab-${tab.id}`} type="button" role="tab" aria-selected={view === tab.id} aria-controls="analytics-content" tabIndex={view === tab.id ? 0 : -1} onClick={() => setView(tab.id)} onKeyDown={e => { let next: number; if (e.key === "ArrowRight") next = (i + 1) % tabs.length; else if (e.key === "ArrowLeft") next = (i + tabs.length - 1) % tabs.length; else if (e.key === "Home") next = 0; else if (e.key === "End") next = tabs.length - 1; else return; e.preventDefault(); setView(tabs[next].id); tabButtons.current[next]?.focus(); }}>{tab.label}</button>)}</div>
    <div id="analytics-content" role="tabpanel" aria-labelledby={`analytics-tab-${view}`} className="analytics-content" tabIndex={0}>
    {!snapshot && !error && <p role="status">Загружаем аналитику…</p>}
    {data && snapshot && <>
      {view === "overview" && snapshot.profiles && <OverviewView data={data} profiles={snapshot.profiles} onOnlineOnly={() => { setOnlineOnly(true); setProfileOffset(0); setView("profiles"); }} openSessions={openSessions} />}
      {view === "activity" && <ActivityView data={data} chartMetric={chartMetric} setChartMetric={setChartMetric} />}
      {view === "profiles" && snapshot.profiles && <ProfilesView data={data} profiles={snapshot.profiles} onlineOnly={onlineOnly} onOnlineOnlyChange={value => { setOnlineOnly(value); setProfileOffset(0); }} profileOffset={profileOffset} setProfileOffset={setProfileOffset} openSessions={openSessions} />}
      {view === "sessions" && snapshot.sessions && <SessionsView data={data} sessions={snapshot.sessions} selected={selected} onBack={() => { if (selected) setView(selected.from); }} onClear={() => { setSelected(null); setSessionOffset(0); }} sessionOffset={sessionOffset} setSessionOffset={setSessionOffset} />}
    </>}
    </div>
  </div>;
}
