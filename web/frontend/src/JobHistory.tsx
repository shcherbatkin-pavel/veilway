import { RestartJob } from "./api";

const labels: Record<string, string> = { queued: "В очереди", dispatching: "Запускается", waiting: "Ожидает восстановления", succeeded: "Выполнена", failed: "Ошибка", needs_review: "Нужна проверка" };
export function JobHistory({ jobs }: { jobs: RestartJob[] }) {
  return (
    <section className="history">
      <div className="section-heading"><div><p className="eyebrow">ПЕРЕЗАГРУЗКИ</p><h2>История перезагрузок</h2></div></div>
      {jobs.length === 0 ? <p className="empty">Операций пока нет</p> : jobs.map((job) => (
        <article className="job" key={job.id}>
          <div><strong>{job.targets.map((target) => target.slug).join(" → ")}</strong><span>{new Date(job.created_at).toLocaleString("ru-RU")}</span>{job.error_code && <span className="error">Операция не завершена. Требуется проверка состояния узлов.</span>}</div>
          <span className={`job-status ${job.status}`}>{labels[job.status] ?? "Нет данных"}</span>
        </article>
      ))}
    </section>
  );
}
