"use client";

import { useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { btnSecondary } from "@/components/layout/AppShell";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import { undoImport } from "@/features/actions";

/**
 * Takes a costs import back: its costs are deleted and the estimates they had replaced are open again.
 * A mapping taken the wrong way — the amount read from the VAT column — is 2 000 wrong costs; this is
 * the way back. A refusal names what stands in the way (an ERP document, a closed month) and stays on
 * screen long enough to be read.
 */
export default function UndoImportButton({ importId, costCount, estimatesReplaced, onUndone }: { importId: string; costCount: number; estimatesReplaced: number; onUndone?: () => void }) {
  const t = useTranslations("imports.undo");
  const router = useRouter();
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [pending, start] = useTransition();
  return (
    <>
      <button ref={trigger} type="button" onClick={() => setOpen(true)} className={btnSecondary}>{t("action")}</button>
      <ConfirmDialog
        open={open}
        title={t("title")}
        body={t("body", { count: costCount, estimates: estimatesReplaced })}
        confirmLabel={t("confirm")}
        cancelLabel={t("cancel")}
        pending={pending}
        returnFocusRef={trigger}
        onCancel={() => setOpen(false)}
        onConfirm={() =>
          start(async () => {
            const r = await undoImport(importId);
            setOpen(false);
            if (!r.ok) return void toast.error(r.error, { duration: 12000 });
            toast.success(t("done", { costs: r.data.costsDeleted, estimates: r.data.estimatesReopened }));
            onUndone?.();
            router.refresh();
          })
        }
      />
    </>
  );
}
