import { VmSlug } from "./api";
import { Modal } from "./Modal";
export function RestartConfirmation({ targets, busy, onCancel, onConfirm }: {
  targets: VmSlug[]; busy: boolean; onCancel: () => void; onConfirm: () => void;
}) {
  return <Modal title={`Перезагрузить ${targets.length === 2 ? "все VPN-узлы" : targets[0]}?`} busy={busy} onClose={onCancel}>
    <p>Активные VPN-соединения будут временно разорваны. При массовой операции AWS будет восстановлен до перезагрузки Yandex.</p>
    <div className="modal-actions"><button className="ghost" disabled={busy} onClick={onCancel}>Отмена</button><button className="danger" disabled={busy} onClick={onConfirm}>{busy ? "Отправляем…" : "Перезагрузить"}</button></div>
  </Modal>;
}
