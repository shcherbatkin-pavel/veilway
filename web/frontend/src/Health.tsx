import { ReactNode } from "react";
import { Assessment, observabilityApi } from "./api";
import { labels, reasons, components, workerStates, crlStates, failureCode, problemsFirst, presentHealth } from "./healthPresentation";
import { Section } from "./Layout";
import { useResource } from "./useResource";

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
function FailureCode({ code }: { code: string | null }) {
  // Only known fixed codes are rendered, never arbitrary server error strings.
  const label = failureCode(code);
  return label ? <><dt>Код ошибки</dt><dd>{label}</dd></> : null;
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
function showDetails(id: string) {
  const details = document.getElementById(id) as HTMLDetailsElement | null;
  if (!details) return;
  details.open = true;
  details.querySelector("summary")?.focus({ preventScroll: true });
  details.scrollIntoView({ block: "nearest", behavior: "instant" });
}
export function Health({ onLogout, onSection }: { onLogout: () => void; onSection: (section: Section) => void }) {
  const { data, error, loading, refresh } = useResource(observabilityApi.overview, onLogout);
  const snapshot = data ? presentHealth(data) : null;
  return <div className="health-page">
    <header className="page-heading"><div><p className="eyebrow">ОБЗОР ЗДОРОВЬЯ</p><h1>Состояние</h1><p className="muted">Проблемы и результаты последних операций.</p></div><button className="ghost" onClick={() => void refresh()}>Обновить</button></header>
    {loading && <p role="status">Загружаем состояние…</p>}
    {error && <div className="banner error" role="alert">Свежие данные недоступны. {data ? "Показан устаревший снимок. Статусы относятся к последнему успешному обновлению." : "Не удалось получить состояние."} {error}</div>}
    {data && snapshot && <>
      <section className="health-summary" aria-label="Общая оценка"><Badge value={error ? "unknown" : data.assessment} /><span>Снимок: {date(data.generated_at)}</span><span className="muted">Обновление каждые 5 секунд</span></section>
      <section className="health-section health-problems" aria-label="Проблемы и действия">
        {snapshot.problems.length > 0 ? <><h2>Проблемы и действия{error ? " · последний снимок" : ""}</h2><ul>{problemsFirst(snapshot.problems).map(problem => <li key={problem.id}>
          <div className="health-problem-heading"><strong>{problem.title}</strong><Badge value={problem.assessment} /></div>
          <p>{problem.messages.join(" · ") || "Состояние требует проверки"}</p><p className="muted">{problem.action}.</p>
          <div className="actions"><button className="ghost" onClick={() => showDetails(problem.id)}>Подробности</button><button className="ghost" onClick={() => onSection(problem.section)}>{problem.section === "nodes" ? "Открыть узлы" : problem.section === "profiles" ? "Профили" : "История"}</button></div>
        </li>)}</ul></> : <p>{!error && data.assessment === "ok" ? "Проблем не обнаружено" : "Свежих данных недостаточно для подтверждения состояния"}</p>}
      </section>
      <section className="health-section"><div className="section-heading"><h2>VPN-узлы</h2><button className="ghost" onClick={() => onSection("nodes")}>Открыть узлы</button></div>
        {problemsFirst(snapshot.nodes).map(node => <Row key={node.slug} id={node.id} title={node.title} assessment={node.assessment}
          summary={node.reasons.includes("planned_restart") ? "Текущая операция: плановый перезапуск" : `Возраст сигнала: ${duration(node.heartbeat_age_seconds)}`}
          messages={node.messages.filter(message => message !== reasons.planned_restart)}>
          <dl><dt>Последний сигнал</dt><dd>{date(node.last_heartbeat_at)}</dd><dt>Возраст сигнала</dt><dd>{duration(node.heartbeat_age_seconds)}</dd><dt>Время работы при последнем сигнале</dt><dd>{duration(node.uptime_seconds)}</dd></dl>
          {node.containers ? <ul>{Object.entries(node.containers).map(([name, state]) => <li key={name}>{name}: {components[state]}</li>)}</ul> : <p>Детализация отсутствует</p>}
        </Row>)}
        <Help>Состояние контейнеров не подтверждает возможность подключения клиента и выхода в интернет. Uptime относится к последнему сигналу. Во время планового перезапуска состояние компонентов требует нового подтверждения.</Help>
      </section>
      <section className="health-section"><h2>Фоновые задачи</h2>{problemsFirst(snapshot.workers).map(worker => <Row key={worker.name} id={worker.id} title={worker.title} assessment={worker.assessment}
        summary={<>{workerStates[worker.state]}{worker.in_progress ? " · Выполняется проход" : ""}<span className="muted">Последний успешный проход: {date(worker.succeeded_at)}</span></>} messages={worker.messages}>
        <dl><dt>Начало последнего прохода</dt><dd>{date(worker.started_at)}</dd><dt>Последний завершённый проход</dt><dd>{date(worker.completed_at)}</dd><dt>Последний успешный проход</dt><dd>{date(worker.succeeded_at)}</dd><FailureCode code={worker.error_code} /></dl>
      </Row>)}<Help>Успешный проход означает завершение обработки. Результат выдачи, отзыва или публикации может содержать ошибку — проверьте сводку операций и историю. После перезапуска backend времена проходов собираются заново.</Help></section>
      <section className="health-section"><div className="section-heading"><h2>Применение отзывов</h2><Badge value={data.crl.assessment} /></div>
        <Row id={snapshot.publication.id} title={snapshot.publication.title} assessment={snapshot.publication.assessment} summary={data.crl.version === null ? "Нет публикации" : "Публикация получена"} messages={snapshot.publication.messages}>
          <dl><dt>Версия публикации</dt><dd>{data.crl.version ?? "Нет публикации"}</dd><dt>Действует до</dt><dd>{date(data.crl.next_update)}</dd><dt>Последнее обращение при публикации</dt><dd>{date(data.crl.attempted_at)}</dd><FailureCode code={data.crl.publisher_error} /></dl>
        </Row>
        {problemsFirst(snapshot.deliveries).map(node => <Row key={node.slug} id={node.id} title={node.slug} assessment={node.assessment} summary={crlStates[node.status] ?? "Нет данных"} messages={node.errorMessages}>
          <dl><dt>Установленная версия</dt><dd>{node.acknowledged_version ?? "Нет данных"}</dd><dt>Последний контакт</dt><dd>{date(node.last_contact_at)}</dd><dt>Последнее подтверждение установки</dt><dd>{date(node.acknowledged_at)}</dd><FailureCode code={node.error_code} /></dl>
        </Row>)}
        <Help>Локальный отзыв ещё не означает его применение на VPN-узле: требуется подтверждение установки CRL. Доступность PKI оценивается по результату обращения при публикации и ошибкам заданий, а не по запуску фоновой задачи.</Help>
      </section>
      <section className="health-section"><div className="section-heading"><h2>Операции</h2><div className="actions"><button className="ghost" onClick={() => onSection("profiles")}>Профили</button><button className="ghost" onClick={() => onSection("history")}>История</button></div></div>
        {problemsFirst(snapshot.operations).map(operation => <Row key={operation.kind} id={operation.id} title={operation.title} assessment={operation.assessment}
          summary={<span className="health-counts"><span>В очереди <strong>{operation.queued}</strong></span><span>Выполняются <strong>{operation.running}</strong></span><span>Задержавшиеся <strong>{operation.delayed}</strong></span><span>Нужна проверка <strong>{operation.needs_review}</strong></span>{operation.kind === "revoke" && <span>Ожидают применения <strong>{operation.awaiting_delivery}</strong></span>}</span>}
          messages={operation.messages}>
          <dl><dt>Возраст самого старого незавершённого задания</dt><dd>{duration(operation.oldest_pending_age_seconds)}</dd><dt>Ошибки за последние сутки</dt><dd>{operation.failed_last_day}</dd><dt>Ожидают повтора после ошибки PKI</dt><dd>{operation.pki_errors}</dd></dl>
        </Row>)}
      </section>
    </>}
  </div>;
}
