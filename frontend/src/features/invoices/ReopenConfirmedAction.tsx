"use client";

import { useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Undo2 } from "lucide-react";
import { toast } from "sonner";
import { btnSecondary } from "@/components/layout/AppShell";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import { reopenInvoice } from "@/features/actions";

/**
 * Reopening a CONFIRMED invoice gives back the costs it created (see `reopen_confirmed` on the
 * backend) — a real, named action, not a status flip, so it asks first. A confirmed invoice whose
 * costs already left for the ERP refuses with COSTS_PUSHED_TO_ERP instead: that message, translated,
 * is what the person sees, never a generic failure. A credit note gives back what it took off the
 * costs it reduced: said so, rather than « the costs will be deleted ». An invoice that did both —
 * created costs and reduced one on file — says both.
 */
export default function ReopenConfirmedAction({ invoiceId, costsCreated, costsCredited = 0, creditNote = false }: { invoiceId: string; costsCreated: number; costsCredited?: number; creditNote?: boolean }) {
  const t = useTranslations("invoices.detail");
  // A credit note is named as one: « Rouvrir cette facture » over an avoir read as the wrong document.
  const kind = creditNote ? "credit" : "invoice";
  const [open, setOpen] = useState(false);
  const [pending, start] = useTransition();
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const router = useRouter();

  const confirm = () =>
    start(async () => {
      const r = await reopenInvoice(invoiceId);
      setOpen(false);
      if (!r.ok) return void toast.error(r.error);
      toast.success(
        costsCredited && costsCreated
          ? t("reopenedMixed", { created: costsCreated, credited: costsCredited })
          : costsCredited ? t("reopenedCredit", { kind, count: costsCredited }) : t("reopenedConfirmed", { count: costsCreated }),
      );
      router.refresh();
    });

  return (
    <>
      <button
        type="button"
        ref={triggerRef}
        onClick={() => setOpen(true)}
        disabled={pending}
        className={btnSecondary}
      >
        <Undo2 className="w-4 h-4 mr-2" />
        {t("reopenConfirmedAction")}
      </button>
      <ConfirmDialog
        open={open}
        title={t("reopenConfirmedTitle", { kind })}
        body={
          costsCredited && costsCreated
            ? t("reopenMixedBody", { created: costsCreated, credited: costsCredited })
            : costsCredited ? t("reopenCreditBody", { kind, count: costsCredited }) : t("reopenConfirmedBody", { count: costsCreated })
        }
        confirmLabel={costsCredited ? t("reopenCreditConfirm") : t("reopenConfirmedConfirm")}
        cancelLabel={t("reopenConfirmedCancel")}
        pending={pending}
        onConfirm={confirm}
        onCancel={() => setOpen(false)}
        returnFocusRef={triggerRef}
      />
    </>
  );
}
