import { Session, profilesApi } from "./api";
import { useResource } from "./useResource";
const labels: Record<string, string> = { unconfigured: "Агент не настроен", pending: "Ожидает установки", current: "Актуальная версия", offline: "Узел недоступен", expired: "Срок CRL истёк", error: "Ошибка доставки" };
const errors: Record<string, string> = { transport_unavailable: "Нет связи с панелью", invalid_bundle: "Узел отклонил список отзывов", installation_failed: "Не удалось установить список отзывов" };
export function Delivery({ onLogout }: { session: Session; onLogout: () => void }) {
  const { data, error, loading } = useResource(profilesApi.crl, onLogout);
  return <section className="panel delivery"><div className="section-heading"><div><p className="eyebrow">ОТЗЫВЫ ПРОФИЛЕЙ</p><h2>Применение отзывов</h2></div><span className="muted">{data?.version ? `Версия ${data.version}` : "Нет публикации"}</span></div>
    <p className="muted">Профиль получает статус «Отозван» после подтверждения нужного VPN-узла.</p>
    {loading && <p role="status">Загружаем состояние доставки…</p>}{error && <p className="error" role="alert">{error}</p>}
    {data?.publisher_error && <div className="banner error" role="alert">Сервис отзывов временно недоступен. Завершение новых отзывов может задержаться.</div>}
    <div className="delivery-grid">{data?.nodes.map(node => <article key={node.slug}><strong>{node.slug}</strong><span className={`status-pill ${node.status === "current" ? "active" : "revoking"}`}>{labels[node.status] ?? "Нет данных"}</span><p className="muted">Установленная версия: {node.acknowledged_version ?? "—"}</p>{node.error_code && <p className="error">{errors[node.error_code] ?? "Узел сообщил об ошибке доставки"}</p>}</article>)}</div>
  </section>;
}
