import { VpnVm } from "./api";

const stateLabels: Record<VpnVm["state"], string> = {
  healthy: "Работает",
  degraded: "Требует внимания",
  unknown: "Нет данных",
  restarting: "Перезагружается",
};

export function VmCard({ vm, selected, disabled, onToggle }: {
  vm: VpnVm; selected: boolean; disabled: boolean; onToggle: () => void;
}) {
  return (
    <article className={`vm-card ${vm.state}`}>
      <div className="vm-title">
        <label className="check">
          <input type="checkbox" checked={selected} disabled={disabled || vm.state !== "healthy"} onChange={onToggle} />
          <span />
        </label>
        <div><h3>{vm.slug}</h3><p>{vm.provider === "aws" ? "Amazon Web Services" : "Yandex Cloud"}</p></div>
        <span className="status-pill">{stateLabels[vm.state]}</span>
      </div>
      <div className="heartbeat"><span>Последний heartbeat</span><strong>{vm.last_heartbeat_at ? new Date(vm.last_heartbeat_at).toLocaleString("ru-RU") : "—"}</strong></div>
    </article>
  );
}
