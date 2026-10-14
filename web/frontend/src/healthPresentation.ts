import type { Assessment, HealthOverview, NodeHealth, WorkerHealth, OperationHealth } from "./api";
import type { Section } from "./Layout";

export const labels: Record<Assessment, string> = { ok: "В норме", attention: "Требует внимания", unknown: "Недостаточно данных" };
export const reasons: Record<string, string> = {
  heartbeat_missing: "Сигнал от узла ещё не получен", heartbeat_stale: "Сигнал от узла устарел",
  component_unhealthy: "Один или несколько компонентов неисправны", details_missing: "Детализация отсутствует",
  planned_restart: "Выполняется плановый перезапуск", node_unconfigured: "Узел не зарегистрирован",
};
export const components = { healthy: "Работает", starting: "Запускается", unhealthy: "Неисправен", missing: "Отсутствует" };
export const workerStates = { starting: "Запускается", running: "Запущена", stopped: "Остановлена", stopping: "Завершается" };
const workerNames: Record<string, string> = { "analytics-retention": "Очистка истории аналитики", "restart-worker": "Перезапуски", "profile-worker": "Выдача и отзыв профилей", "crl-worker": "Публикация отзывов" };
export const crlStates: Record<string, string> = { current: "Актуальная версия", pending: "Ожидает установки", expired: "Срок действия истёк", offline: "Нет свежего контакта", error: "Ошибка доставки", unconfigured: "Агент не настроен" };
const errors: Record<string, string> = {
  step_failed: "Проход завершился ошибкой", worker_failed: "Фоновая задача завершилась ошибкой",
  worker_stopped: "Фоновая задача неожиданно остановилась", worker_cancelled: "Фоновая задача прервана",
  worker_stop_failed: "Ошибка завершения фоновой задачи", publication_unavailable: "Публикация отзывов недоступна",
  transport_unavailable: "Нет связи с панелью", invalid_bundle: "Узел отклонил список отзывов",
  installation_failed: "Не удалось установить список отзывов", delivery_failed: "Ошибка доставки",
};
const operationNames = { issue: "Выдача профилей", revoke: "Отзывы профилей", restart: "Перезапуски узлов" };
export function problemsFirst<T extends { assessment: Assessment }>(items: T[]): T[] {
  const order = { attention: 0, unknown: 1, ok: 2 };
  return [...items].sort((a, b) => order[a.assessment] - order[b.assessment]);
}
function failure(code: string | null) { return code ? Object.hasOwn(errors, code) ? errors[code] : "Операция завершилась ошибкой" : ""; }
export function failureCode(code: string | null) {
  return code ? Object.hasOwn(errors, code) ? code : "Неизвестный код" : null;
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

export function deliveryAssessment(status: string): Assessment {
  return status === "current" ? "ok" : ["pending", "expired", "offline", "error"].includes(status) ? "attention" : "unknown";
}

type HealthRow = { id: string; title: string; assessment: Assessment; messages: string[] };
export type HealthProblem = HealthRow & { action: string; section: Section };

function problemsIn(rows: HealthRow[], action: string, section: Section): HealthProblem[] {
  return rows.map(({ id, title, assessment, messages }) => ({ id, title, assessment, messages, action, section }));
}

// Prepare each explanation once so the problem list and details share a snapshot.
export function presentHealth(data: HealthOverview) {
  const nodes = data.nodes.map(node => ({
    ...node, id: `health-node-${node.slug}`, title: node.slug, messages: nodeReasons(node),
  }));
  const workers = data.workers.map(worker => ({
    ...worker, id: `health-worker-${worker.name}`, title: workerNames[worker.name] ?? "Фоновая задача", messages: workerReasons(worker),
  }));
  const crl = data.crl;
  const publication: HealthRow = {
    id: "health-publication", title: "Публикация CRL",
    assessment: crl.publisher_error || crl.publication_expired ? "attention" : crl.observation_stale || crl.version === null ? "unknown" : "ok",
    messages: [failure(crl.publisher_error), crl.publication_expired ? "Срок действия публикации истёк" : "",
      crl.observation_stale ? "Нет свежего результата проверки публикации. Доступность PKI не подтверждена" : "",
      crl.version === null ? "Нет публикации" : ""].filter(Boolean),
  };
  const deliveries = crl.nodes.map(node => {
    const errorMessages = [failure(node.error_code)].filter(Boolean);
    return {
      ...node, id: `health-delivery-${node.slug}`, title: `Применение отзывов · ${node.slug}`,
      assessment: deliveryAssessment(node.status), errorMessages,
      messages: [crlStates[node.status] ?? "Нет данных", ...errorMessages],
    };
  });
  const operations = data.operations.map(operation => ({
    ...operation, id: `health-operation-${operation.kind}`, title: operationNames[operation.kind], messages: operationReasons(operation),
  }));
  const problems = [
    ...problemsIn(nodes, "Проверьте heartbeat и компоненты узла", "nodes"),
    ...problemsIn(workers, "Проверьте доступность backend и его зависимостей; результат задания — в истории", "history"),
    ...problemsIn([{ ...publication, title: "Публикация отзывов" }], "Проверьте публикацию CRL и подтверждения агентов", "nodes"),
    ...problemsIn(deliveries, "Проверьте подтверждение установки CRL на узле", "nodes"),
    ...operations.flatMap(operation => problemsIn([operation], "Проверьте результат и причину задержки. При неопределённом результате нужна ручная проверка", operation.kind === "restart" ? "nodes" : "profiles")),
  ].filter(problem => problem.assessment !== "ok");
  return { nodes, workers, publication, deliveries, operations, problems };
}
