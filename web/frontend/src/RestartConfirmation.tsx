import { VmSlug } from "./api";

export function RestartConfirmation({ targets, busy, onCancel, onConfirm }: {
  targets: VmSlug[]; busy: boolean; onCancel: () => void; onConfirm: () => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal" role="dialog" aria-modal="true">
        <p className="eyebrow">ПОДТВЕРЖДЕНИЕ</p>
        <h2>Перезагрузить {targets.length === 2 ? "все VPN-узлы" : targets[0]}?</h2>
        <p>Активные VPN-соединения будут временно разорваны. При массовой операции AWS будет восстановлен до перезагрузки Yandex.</p>
        <div className="modal-actions">
          <button className="ghost" disabled={busy} onClick={onCancel}>Отмена</button>
          <button className="danger" disabled={busy} onClick={onConfirm}>{busy ? "Отправляем…" : "Перезагрузить"}</button>
        </div>
      </section>
    </div>
  );
}
