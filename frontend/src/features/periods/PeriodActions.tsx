"use client";

import { useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { LockKeyhole, LockKeyholeOpen } from "lucide-react";
import { toast } from "sonner";
import { btnPrimary, btnSecondary } from "@/components/layout/AppShell";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import { closePeriod, reopenPeriod } from "@/features/actions";

/**
 * Close or reopen a month. Both are decisions with an accounting meaning, so both ask once more and
 * say in plain words what will and will not happen. The buttons only show for the roles the API
 * accepts (owner or admin, for both); the API refuses anyway if the screen is wrong.
 *
 * A month the API will refuse to close (`blockedBy`: orders with no exchange rate) offers no close at
 * all, and says why where the button would be. A refusal the screen did not foresee stays on the page
 * next to the button, not in a toast that is gone before it is read.
 */
export default function PeriodActions({ period, title, status, canClose, canReopen, estimated, blockedBy = [] }: { period: string; title: string; status: "open" | "closed"; canClose: boolean; canReopen: boolean; estimated: string | null; blockedBy?: string[] }) {
  const t = useTranslations("periods.actions");
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, start] = useTransition();
  const [refused, setRefused] = useState<string | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const closing = status === "open";
  if (closing ? !canClose : !canReopen) return closing ? <p className="text-xs text-slate-500 max-w-xs text-right">{t("closeNeedsRole")}</p> : null;
  if (closing && blockedBy.length > 0) {
    return <p role="note" className="text-sm text-amber-900 max-w-sm text-right">{t.rich("blocked", { count: blockedBy.length, orders: blockedBy.join(", "), o: (chunks) => <span className="whitespace-nowrap">{chunks}</span> })}</p>;
  }

  return (
    <div className="flex flex-col items-end gap-1.5">
      <button ref={trigger} type="button" onClick={() => setOpen(true)} className={closing ? btnPrimary : btnSecondary}>
        {closing ? <LockKeyhole className="w-4 h-4 mr-1.5" aria-hidden /> : <LockKeyholeOpen className="w-4 h-4 mr-1.5" aria-hidden />}
        {closing ? t("close", { month: title }) : t("reopen")}
      </button>
      <ConfirmDialog
        open={open}
        danger={!closing}
        title={closing ? t("closeTitle", { month: title }) : t("reopenTitle", { month: title })}
        body={closing ? (estimated ? t("closeBodyEstimated", { amount: estimated }) : t("closeBody")) : t("reopenBody")}
        confirmLabel={pending ? t("working") : closing ? t("closeConfirm") : t("reopenConfirm")}
        cancelLabel={t("cancel")}
        pending={pending}
        returnFocusRef={trigger}
        onCancel={() => setOpen(false)}
        onConfirm={() =>
          start(async () => {
            const r = closing ? await closePeriod(period) : await reopenPeriod(period);
            setOpen(false);
            if (!r.ok) return void setRefused(r.error);
            setRefused(null);
            toast.success(closing ? t("closed", { month: title }) : t("reopened", { month: title }));
            router.refresh();
          })
        }
      />
      {refused ? <p role="alert" className="text-sm text-red-700 max-w-sm text-right">{refused}</p> : null}
    </div>
  );
}
