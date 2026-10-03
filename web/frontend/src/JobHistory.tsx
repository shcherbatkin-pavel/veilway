import { RestartJob } from "./api";

export function JobHistory({ jobs }: { jobs: RestartJob[] }) {
  return (
    <section className="history">
      <div className="section-heading"><div><p className="eyebrow">OPERATIONS</p><h2>История</h2></div></div>
      {jobs.length === 0 ? <p className="empty">Операций пока нет</p> : jobs.map((job) => (
        <article className="job" key={job.id}>
          <div><strong>{job.targets.map((target) => target.slug).join(" → ")}</strong><span>{new Date(job.created_at).toLocaleString("ru-RU")}</span></div>
          <span className={`job-status ${job.status}`}>{job.status}</span>
        </article>
      ))}
    </section>
  );
}
