"use client";

import { useEffect, useRef, type RefObject } from "react";
import { btnPrimary, btnSecondary } from "@/components/layout/AppShell";
import { useFocusTrap } from "@/hooks/useFocusTrap";

/**
 * A blocking confirmation for a destructive, one-click action (see audit B16: deleting a cost used
 * to happen at once, with no confirmation and no way back). Focus moves to Cancel on open, Tab is
 * trapped inside, Escape and the backdrop both cancel, and focus returns to whatever opened it.
 */
export default function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  cancelLabel,
  danger = true,
  pending = false,
  onConfirm,
  onCancel,
  returnFocusRef,
}: {
  open: boolean;
  title: string;
  body: React.ReactNode;
  confirmLabel: string;
  cancelLabel: string;
  danger?: boolean;
  pending?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  /** The button that opened the dialog: focus returns there when it closes. */
  returnFocusRef?: RefObject<HTMLElement | null>;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const titleId = "confirm-dialog-title";

  useFocusTrap(open, panelRef, returnFocusRef);

  useEffect(() => {
    if (open) cancelRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <button type="button" className="absolute inset-0 bg-slate-900/40" aria-label={cancelLabel} onClick={onCancel} />
      <div ref={panelRef} role="alertdialog" aria-modal="true" aria-labelledby={titleId} className="relative bg-white rounded-lg shadow-xl max-w-sm w-full p-5">
        <h2 id={titleId} className="text-base font-semibold text-slate-900">{title}</h2>
        <div className="mt-2 text-sm text-slate-600">{body}</div>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" ref={cancelRef} onClick={onCancel} className={btnSecondary}>{cancelLabel}</button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={pending}
            className={`${btnPrimary} ${danger ? "!bg-red-600 hover:!bg-red-700 focus-visible:!ring-red-500" : ""}`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
