"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { btnPrimary, btnSecondary } from "@/components/layout/AppShell";
import { reopenInvoice, retryInvoice } from "@/features/actions";

export default function RejectedInvoiceActions({ invoiceId, hasLines }: { invoiceId: string; hasLines: boolean }) {
  const t = useTranslations("invoices.detail");
  const [pending, start] = useTransition();
  const router = useRouter();

  const run = (fn: () => Promise<{ ok: boolean; error?: string }>, okMsg: string) =>
    start(async () => {
      const r = await fn();
      if (!r.ok) return void toast.error(r.error ?? t("failed"));
      toast.success(okMsg);
      router.refresh();
    });

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <button
        type="button"
        disabled={pending || !hasLines}
        onClick={() => run(() => reopenInvoice(invoiceId), t("reopened"))}
        className={btnPrimary}
      >
        {pending ? <Loader2 className="w-4 h-4 mr-2 animate-spin" aria-hidden /> : null}
        {t("reopenAction")}
      </button>
      <button type="button" disabled={pending} onClick={() => run(() => retryInvoice(invoiceId), t("retried"))} className={btnSecondary}>
        {t("retry")}
      </button>
    </div>
  );
}
