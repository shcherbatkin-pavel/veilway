import { ReactNode, useEffect, useRef } from "react";

export function Modal({ title, busy = false, onClose, children }: { title: string; busy?: boolean; onClose: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { const node = dialog.current!; node.showModal(); return () => node.close(); }, []);
  return <dialog ref={dialog} className="modal" aria-label={title} onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className="modal-heading"><h2>{title}</h2><button type="button" className="icon-button" aria-label="Закрыть" disabled={busy} onClick={onClose}>×</button></div>
    {children}
  </dialog>;
}
