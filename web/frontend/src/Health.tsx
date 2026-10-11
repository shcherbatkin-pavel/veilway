import { ReactNode } from "react";
import { Assessment, NodeHealth, WorkerHealth, OperationHealth, observabilityApi } from "./api";
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
const workerNames: Record<string, string> = { "analytics-retention": "Очистка истории аналитики", "restart-worker": "Перезапуски", "profile-worker": "Выдача и отзыв профилей", "crl-worker": "Публикация отзывов" };
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
function failure(code: string | null) { return code ? Object.hasOwn(errors, code) ? errors[code] : "Операция завершилась ошибкой" : ""; }
function FailureCode({ code }: { code: string | null }) {
  // Only known fixed codes are rendered, never arbitrary server error strings.
  return code ? <><dt>Код ошибки</dt><dd>{Object.hasOwn(errors, code) ? code : "Неизвестный код"}</dd></> : null;
}
function nodeReasons(node: NodeHealth) {
  const messages = node.reasons.map(reason => reasons[reason] ?? "Состояние требует проверки");
  if (!node.reasons.includes("planned_restart")) {
    Object.entries(node.containers ?? {}).forEach(([name, state]) => {
      if (state !== "healthy") messages.push(`${name}: ${components[state]}`);
    });
  }
  return messages;
}
function workerReasons(worker: WorkerHealth) {
  return [failure(worker.error_code), worker.slow ? "Проход длится больше трёх минут" : "",
    worker.assessment === "unknown" ? `${workerStates[worker.state]}. Нет свежего подтверждения завершённого прохода` : ""].filter(Boolean);
}
function operationReasons(operation: OperationHealth) {
  return [operation.delayed ? `Задержавшиеся: ${operation.delayed}` : "",
    operation.needs_review ? `Требуют ручной проверки: ${operation.needs_review}` : "",
    operation.failed_last_day ? `Ошибки за последние сутки: ${operation.failed_last_day}` : "",
    operation.pki_errors ? `Ожидают повтора после ошибки PKI: ${operation.pki_errors}` : "",
    operation.assessment === "unknown" ? "Недостаточно данных об операциях" : ""].filter(Boolean);
}
function Row({ id, title, assessment, summary, messages = [], children }: {
  id: string; title: string; assessment: Assessment; summary: ReactNode; messages?: string[]; children: ReactNode;
}) {
  return <details className="health-row" id={id}>
    <summary><span className="health-row-title">{title}<Badge value={assessment} /></span>
      <span className="health-row-summary">{summary}{messages.length > 0 && <span className={`health-row-reasons health-${assessment}`}>{messages.join(" · ")}</span>}</span>
      <span className="health-disclosure" aria-hidden="true">Подробности</span>
    </summary>
    <div className="health-row-details">{children}</div>
  </details>;
}
function Help({ children }: { children: ReactNode }) {
  return <details className="health-help"><summary>Как читать показатели</summary><p>{children}</p></details>;
}
type Problem = { id: string; title: string; assessment: Assessment; messages: string[]; action: string; section: Section };
function showDetails(id: string) {
  const details = document.getElementById(id) as HTMLDetailsElement | null;
  if (!details) return;
  details.open = true;
  details.querySelector("summary")?.focus({ preventScroll: true });
  details.scrollIntoView({ block: "nearest", behavior: "instant" });
}
export function Health({ onLogout, onSection }: { onLogout: () => void; onSection: (section: Section) => void }) {
  const { data, error, loading, refresh } = useResource(observabilityApi.overview, onLogout);
  const publicationAssessment: Assessment = !data ? "unknown" : data.crl.publisher_error || data.crl.publication_expired ? "attention" : data.crl.observation_stale || data.crl.version === null ? "unknown" : "ok";
  const publicationMessages = data ? [failure(data.crl.publisher_error), data.crl.publication_expired ? "Срок действия публикации истёк" : "",
    data.crl.observation_stale ? "Нет свежего результата проверки публикации. Доступность PKI не подтверждена" : "",
    data.crl.version === null ? "Нет публикации" : ""].filter(Boolean) : [];
  const deliveryAssessment = (status: string): Assessment => status === "current" ? "ok" : ["pending", "expired", "offline", "error"].includes(status) ? "attention" : "unknown";
  const problems: Problem[] = data ? [
    ...data.nodes.map(node => ({ id: `health-node-${node.slug}`, title: node.slug, assessment: node.assessment, messages: nodeReasons(node), action: "Проверьте heartbeat и компоненты узла", section: "nodes" as Section })),
    ...data.workers.map(worker => ({ id: `health-worker-${worker.name}`, title: workerNames[worker.name] ?? "Фоновая задача", assessment: worker.assessment, messages: workerReasons(worker), action: "Проверьте доступность backend и его зависимостей; результат задания — в истории", section: "history" as Section })),
    { id: "health-publication", title: "Публикация отзывов", assessment: publicationAssessment, messages: publicationMessages, action: "Проверьте публикацию CRL и подтверждения агентов", section: "nodes" as Section },
    ...data.crl.nodes.map(node => ({ id: `health-delivery-${node.slug}`, title: `Применение отзывов · ${node.slug}`, assessment: deliveryAssessment(node.status), messages: [crlStates[node.status] ?? "Нет данных", failure(node.error_code)].filter(Boolean), action: "Проверьте подтверждение установки CRL на узле", section: "nodes" as Section })),
    ...data.operations.map(operation => ({ id: `health-operation-${operation.kind}`, title: operationNames[operation.kind], assessment: operation.assessment, messages: operationReasons(operation), action: "Проверьте результат и причину задержки. При неопределённом результате нужна ручная проверка", section: (operation.kind === "restart" ? "nodes" : "profiles") as Section })),
  ].filter(problem => problem.assessment !== "ok") : [];
  return <div className="health-page">
    <header className="page-heading"><div><p className="eyebrow">ОБЗОР ЗДОРОВЬЯ</p><h1>Состояние</h1><p className="muted">Проблемы и результаты последних операций.</p></div><button className="ghost" onClick={() => void refresh()}>Обновить</button></header>
    {loading && <p role="status">Загружаем состояние…</p>}
    {error && <div className="banner error" role="alert">Свежие данные недоступны. {data ? "Показан устаревший снимок. Статусы относятся к последнему успешному обновлению." : "Не удалось получить состояние."} {error}</div>}
    {data && <>
      <section className="health-summary" aria-label="Общая оценка"><Badge value={error ? "unknown" : data.assessment} /><span>Снимок: {date(data.generated_at)}</span><span className="muted">Обновление каждые 5 секунд</span></section>
      <section className="health-section health-problems" aria-label="Проблемы и действия">
        {problems.length > 0 ? <><h2>Проблемы и действия{error ? " · последний снимок" : ""}</h2><ul>{problemsFirst(problems).map(problem => <li key={problem.id}>
          <div className="health-problem-heading"><strong>{problem.title}</strong><Badge value={problem.assessment} /></div>
          <p>{problem.messages.join(" · ") || "Состояние требует проверки"}</p><p className="muted">{problem.action}.</p>
          <div className="actions"><button className="ghost" onClick={() => showDetails(problem.id)}>Подробности</button><button className="ghost" onClick={() => onSection(problem.section)}>{problem.section === "nodes" ? "Открыть узлы" : problem.section === "profiles" ? "Профили" : "История"}</button></div>
        </li>)}</ul></> : <p>{!error && data.assessment === "ok" ? "Проблем не обнаружено" : "Свежих данных недостаточно для подтверждения состояния"}</p>}
      </section>
      <section className="health-section"><div className="section-heading"><h2>VPN-узлы</h2><button className="ghost" onClick={() => onSection("nodes")}>Открыть узлы</button></div>
        {problemsFirst(data.nodes).map(node => <Row key={node.slug} id={`health-node-${node.slug}`} title={node.slug} assessment={node.assessment}
          summary={node.reasons.includes("planned_restart") ? "Текущая операция: плановый перезапуск" : `Возраст сигнала: ${duration(node.heartbeat_age_seconds)}`}
          messages={nodeReasons(node).filter(message => message !== reasons.planned_restart)}>
          <dl><dt>Последний сигнал</dt><dd>{date(node.last_heartbeat_at)}</dd><dt>Возраст сигнала</dt><dd>{duration(node.heartbeat_age_seconds)}</dd><dt>Время работы при последнем сигнале</dt><dd>{duration(node.uptime_seconds)}</dd></dl>
          {node.containers ? <ul>{Object.entries(node.containers).map(([name, state]) => <li key={name}>{name}: {components[state]}</li>)}</ul> : <p>Детализация отсутствует</p>}
        </Row>)}
        <Help>Состояние контейнеров не подтверждает возможность подключения клиента и выхода в интернет. Uptime относится к последнему сигналу. Во время планового перезапуска состояние компонентов требует нового подтверждения.</Help>
      </section>
      <section className="health-section"><h2>Фоновые задачи</h2>{problemsFirst(data.workers).map(worker => <Row key={worker.name} id={`health-worker-${worker.name}`} title={workerNames[worker.name] ?? "Фоновая задача"} assessment={worker.assessment}
        summary={<>{workerStates[worker.state]}{worker.in_progress ? " · Выполняется проход" : ""}<span className="muted">Последний успешный проход: {date(worker.succeeded_at)}</span></>} messages={workerReasons(worker)}>
        <dl><dt>Начало последнего прохода</dt><dd>{date(worker.started_at)}</dd><dt>Последний завершённый проход</dt><dd>{date(worker.completed_at)}</dd><dt>Последний успешный проход</dt><dd>{date(worker.succeeded_at)}</dd><FailureCode code={worker.error_code} /></dl>
      </Row>)}<Help>Успешный проход означает завершение обработки. Результат выдачи, отзыва или публикации может содержать ошибку — проверьте сводку операций и историю. После перезапуска backend времена проходов собираются заново.</Help></section>
      <section className="health-section"><div className="section-heading"><h2>Применение отзывов</h2><Badge value={data.crl.assessment} /></div>
        <Row id="health-publication" title="Публикация CRL" assessment={publicationAssessment} summary={data.crl.version === null ? "Нет публикации" : "Публикация получена"} messages={publicationMessages}>
          <dl><dt>Версия публикации</dt><dd>{data.crl.version ?? "Нет публикации"}</dd><dt>Действует до</dt><dd>{date(data.crl.next_update)}</dd><dt>Последнее обращение при публикации</dt><dd>{date(data.crl.attempted_at)}</dd><FailureCode code={data.crl.publisher_error} /></dl>
        </Row>
        {problemsFirst(data.crl.nodes.map(node => ({ ...node, assessment: deliveryAssessment(node.status) }))).map(node => <Row key={node.slug} id={`health-delivery-${node.slug}`} title={node.slug} assessment={node.assessment} summary={crlStates[node.status] ?? "Нет данных"} messages={node.error_code ? [failure(node.error_code)] : []}>
          <dl><dt>Установленная версия</dt><dd>{node.acknowledged_version ?? "Нет данных"}</dd><dt>Последний контакт</dt><dd>{date(node.last_contact_at)}</dd><dt>Последнее подтверждение установки</dt><dd>{date(node.acknowledged_at)}</dd><FailureCode code={node.error_code} /></dl>
        </Row>)}
        <Help>Локальный отзыв ещё не означает его применение на VPN-узле: требуется подтверждение установки CRL. Доступность PKI оценивается по результату обращения при публикации и ошибкам заданий, а не по запуску фоновой задачи.</Help>
      </section>
      <section className="health-section"><div className="section-heading"><h2>Операции</h2><div className="actions"><button className="ghost" onClick={() => onSection("profiles")}>Профили</button><button className="ghost" onClick={() => onSection("history")}>История</button></div></div>
        {problemsFirst(data.operations).map(operation => <Row key={operation.kind} id={`health-operation-${operation.kind}`} title={operationNames[operation.kind]} assessment={operation.assessment}
          summary={<span className="health-counts"><span>В очереди <strong>{operation.queued}</strong></span><span>Выполняются <strong>{operation.running}</strong></span><span>Задержавшиеся <strong>{operation.delayed}</strong></span><span>Нужна проверка <strong>{operation.needs_review}</strong></span>{operation.kind === "revoke" && <span>Ожидают применения <strong>{operation.awaiting_delivery}</strong></span>}</span>}
          messages={operationReasons(operation)}>
          <dl><dt>Возраст самого старого незавершённого задания</dt><dd>{duration(operation.oldest_pending_age_seconds)}</dd><dt>Ошибки за последние сутки</dt><dd>{operation.failed_last_day}</dd><dt>Ожидают повтора после ошибки PKI</dt><dd>{operation.pki_errors}</dd></dl>
        </Row>)}
      </section>
    </>}
  </div>;
}
