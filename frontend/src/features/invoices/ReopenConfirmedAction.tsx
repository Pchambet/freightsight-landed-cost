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
 * is what the person sees, never a generic failure.
 */
export default function ReopenConfirmedAction({ invoiceId, costsCreated }: { invoiceId: string; costsCreated: number }) {
  const t = useTranslations("invoices.detail");
  const [open, setOpen] = useState(false);
  const [pending, start] = useTransition();
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const router = useRouter();

  const confirm = () =>
    start(async () => {
      const r = await reopenInvoice(invoiceId);
      setOpen(false);
      if (!r.ok) return void toast.error(r.error);
      toast.success(t("reopenedConfirmed", { count: costsCreated }));
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
        title={t("reopenConfirmedTitle")}
        body={t("reopenConfirmedBody", { count: costsCreated })}
        confirmLabel={t("reopenConfirmedConfirm")}
        cancelLabel={t("reopenConfirmedCancel")}
        pending={pending}
        onConfirm={confirm}
        onCancel={() => setOpen(false)}
        returnFocusRef={triggerRef}
      />
    </>
  );
}
