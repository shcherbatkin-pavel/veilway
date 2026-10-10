import { Assessment, observabilityApi } from "./api";
import { Section } from "./Layout";
import { useResource } from "./useResource";

const labels: Record<Assessment, string> = { ok: "В норме", attention: "Требует внимания", unknown: "Недостаточно данных" };
const reasons: Record<string, string> = {
  heartbeat_missing: "Сигнал от узла ещё не получен", heartbeat_stale: "Сигнал от узла устарел",
  component_unhealthy: "Один или несколько компонентов неисправны", details_missing: "Детализация отсутствует",
  planned_restart: "Выполняется плановый перезапуск", node_unconfigured: "Узел не зарегистрирован",
};
const components = { healthy: "Работает", starting: "Запускается", unhealthy: "Неисправен", missing: "Отсутствует" };
const workerStates = { starting: "Запускается", running: "Запущена", stopped: "Остановлена", stopping: "Завершается" };
const workerNames: Record<string, string> = { "restart-worker": "Перезапуски", "profile-worker": "Выдача и отзыв профилей", "crl-worker": "Публикация отзывов" };
const crlStates: Record<string, string> = { current: "Актуальная версия", pending: "Ожидает установки", expired: "Срок действия истёк", offline: "Нет свежего контакта", error: "Ошибка доставки", unconfigured: "Агент не настроен" };
const errors: Record<string, string> = {
  step_failed: "Проход завершился ошибкой", worker_failed: "Фоновая задача завершилась ошибкой",
  worker_stopped: "Фоновая задача неожиданно остановилась", worker_cancelled: "Фоновая задача прервана",
  worker_stop_failed: "Ошибка завершения фоновой задачи", publication_unavailable: "Публикация отзывов недоступна",
  transport_unavailable: "Нет связи с панелью", invalid_bundle: "Узел отклонил список отзывов",
  installation_failed: "Не удалось установить список отзывов", delivery_failed: "Ошибка доставки",
};
const operationNames = { issue: "Выдача профилей", revoke: "Отзывы профилей", restart: "Перезапуски узлов" };
function date(value: string | null) { return value ? new Date(value).toLocaleString("ru-RU") : "Нет данных"; }
function duration(seconds: number | null) {
  if (seconds === null) return "Нет данных";
  if (seconds < 60) return `${seconds} с`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} мин`;
  return `${Math.floor(seconds / 3600)} ч ${Math.floor(seconds % 3600 / 60)} мин`;
}
function Badge({ value }: { value: Assessment }) {
  return <span className={`status-pill health-${value}`}>{labels[value]}</span>;
}
function problemsFirst<T extends { assessment: Assessment }>(items: T[]): T[] {
  const order = { attention: 0, unknown: 1, ok: 2 };
  return [...items].sort((a, b) => order[a.assessment] - order[b.assessment]);
}
function Failure({ code }: { code: string | null }) {
  return code ? <p className="error">{errors[code] ?? "Операция завершилась ошибкой"} <small>({code})</small></p> : null;
}
export function Health({ onLogout, onSection }: { onLogout: () => void; onSection: (section: Section) => void }) {
  const { data, error, loading, refresh } = useResource(observabilityApi.overview, onLogout);
  return <>
    <header className="page-heading"><div><p className="eyebrow">ОБЗОР ЗДОРОВЬЯ</p><h1>Состояние</h1><p className="muted">Состояние узлов и результаты последних операций.</p></div><button className="ghost" onClick={() => void refresh()}>Обновить</button></header>
    {loading && <p role="status">Загружаем состояние…</p>}
    {error && <div className="banner error" role="alert">Свежие данные недоступны. {data ? "Показан устаревший снимок." : "Не удалось получить состояние."} {error}</div>}
    {data && <>
      <section className="panel health-summary" aria-label="Общая оценка"><Badge value={error ? "unknown" : data.assessment} /><span>Снимок: {date(data.generated_at)}</span><span className="muted">Автообновление каждые 5 секунд</span></section>
      <section className="panel"><div className="section-heading"><h2>VPN-узлы</h2><button className="secondary" onClick={() => onSection("nodes")}>Открыть узлы</button></div>
        <div className="health-grid">{problemsFirst(data.nodes).map(node => <article className="health-card" key={node.slug}>
          <div className="section-heading"><h3>{node.slug}</h3><Badge value={node.assessment} /></div>
          {node.reasons.map(reason => <p key={reason}>{reasons[reason] ?? "Состояние требует проверки"}</p>)}
          <dl><dt>Последний сигнал</dt><dd>{date(node.last_heartbeat_at)}</dd><dt>Возраст сигнала</dt><dd>{duration(node.heartbeat_age_seconds)}</dd><dt>Время работы при последнем сигнале</dt><dd>{duration(node.uptime_seconds)}</dd></dl>
          {node.containers && <ul>{Object.entries(node.containers).sort(([, a], [, b]) => Number(a === "healthy") - Number(b === "healthy")).map(([name, state]) => <li key={name}>{name}: {components[state]}</li>)}</ul>}
          {node.assessment !== "ok" && <p className="muted">Проверьте настройку heartbeat и состояние указанных компонентов на узле по операторской инструкции.</p>}
        </article>)}</div>
        <p className="muted">Состояние контейнеров не подтверждает возможность подключения клиента и выхода в интернет.</p>
      </section>
      <section className="panel"><h2>Фоновые задачи</h2><div className="health-grid">{problemsFirst(data.workers).map(worker => <article className="health-card" key={worker.name}>
        <div className="section-heading"><h3>{workerNames[worker.name]}</h3><Badge value={worker.assessment} /></div>
        <p>{workerStates[worker.state]}{worker.in_progress ? " · Выполняется проход" : ""}</p>
        <dl><dt>Начало последнего прохода</dt><dd>{date(worker.started_at)}</dd><dt>Последний завершённый проход</dt><dd>{date(worker.completed_at)}</dd><dt>Последний успешный проход</dt><dd>{date(worker.succeeded_at)}</dd></dl>
        <Failure code={worker.error_code} />{worker.slow && <p className="error">Проход длится больше трёх минут.</p>}
        {worker.assessment !== "ok" && <p className="muted">Проверьте доступность backend и его зависимостей. Результаты операций приведены ниже.</p>}
      </article>)}</div><p className="muted">Успешный проход означает завершение обработки. Результат выдачи, отзыва или публикации может содержать ошибку. После перезапуска backend времена проходов собираются заново.</p></section>
      <section className="panel"><div className="section-heading"><h2>Применение отзывов</h2><Badge value={data.crl.assessment} /></div>
        <dl><dt>Версия публикации</dt><dd>{data.crl.version ?? "Нет публикации"}</dd><dt>Действует до</dt><dd>{date(data.crl.next_update)}</dd><dt>Последнее обращение при публикации</dt><dd>{date(data.crl.attempted_at)}</dd></dl>
        <Failure code={data.crl.publisher_error} />
        {data.crl.publication_expired && <p className="error">Срок действия публикации истёк.</p>}
        {data.crl.observation_stale && <p className="muted">Нет свежего результата проверки публикации. Доступность PKI не подтверждена.</p>}
        <div className="health-grid">{[...data.crl.nodes].sort((a, b) => Number(a.status === "current") - Number(b.status === "current")).map(node => <article className="health-card" key={node.slug}><h3>{node.slug}</h3><p>{crlStates[node.status] ?? "Нет данных"}</p><dl><dt>Установленная версия</dt><dd>{node.acknowledged_version ?? "Нет данных"}</dd><dt>Последний контакт</dt><dd>{date(node.last_contact_at)}</dd><dt>Последнее подтверждение установки</dt><dd>{date(node.acknowledged_at)}</dd></dl><Failure code={node.error_code} /></article>)}</div>
        {data.crl.assessment !== "ok" && <p className="muted">Проверьте публикацию CRL и подтверждения агентов на странице узлов. Локальный отзыв ещё не означает его применение на VPN-узле.</p>}
      </section>
      <section className="panel"><div className="section-heading"><h2>Операции</h2><div className="actions"><button className="secondary" onClick={() => onSection("profiles")}>Профили</button><button className="secondary" onClick={() => onSection("history")}>История</button></div></div>
        <div className="health-grid">{problemsFirst(data.operations).map(operation => <article className="health-card" key={operation.kind}>
          <div className="section-heading"><h3>{operationNames[operation.kind]}</h3><Badge value={operation.assessment} /></div>
          <dl><dt>В очереди</dt><dd>{operation.queued}</dd><dt>Выполняются</dt><dd>{operation.running}</dd><dt>Возраст самого старого незавершённого задания</dt><dd>{duration(operation.oldest_pending_age_seconds)}</dd><dt>Задержавшиеся</dt><dd>{operation.delayed}</dd><dt>Требуют ручной проверки</dt><dd>{operation.needs_review}</dd><dt>Ошибки за последние сутки</dt><dd>{operation.failed_last_day}</dd><dt>Ожидают повтора после ошибки PKI</dt><dd>{operation.pki_errors}</dd>{operation.kind === "revoke" && <><dt>Ожидают применения на узле</dt><dd>{operation.awaiting_delivery}</dd></>}</dl>
          {operation.assessment !== "ok" && <p className="muted">Откройте {operation.kind === "restart" ? "узлы и историю" : "профили и историю"}, проверьте результат и причину задержки. При неопределённом результате требуется ручная проверка.</p>}
        </article>)}</div>
      </section>
    </>}
  </>;
}
