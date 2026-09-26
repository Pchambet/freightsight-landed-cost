"use client";

import { useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { FlaskConical } from "lucide-react";
import { toast } from "sonner";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import { deleteSampleData, type SampleBlocker } from "@/features/actions";

/**
 * Sample data looks exactly like real data, which is the point of it and the danger of it: a figure
 * read on this screen may be an invented importer's. While a company holds sample data every page
 * says so, and offers the way out. A refusal lists what of the user's own leans on a sample record,
 * by its container or order number; nothing is deleted in that case.
 */
export default function SampleDataBanner() {
  const t = useTranslations("sample");
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [blockers, setBlockers] = useState<SampleBlocker[]>([]);
  const [pending, start] = useTransition();
  const trigger = useRef<HTMLButtonElement>(null);

  return (
    // A landmark of its own: it sits above the page's main, and a screen reader lists it by name.
    <section aria-label={t("region")} className="border-b border-violet-200 bg-violet-50 print:hidden">
      <div className="px-4 sm:px-6 lg:px-8 py-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-violet-950">
        <FlaskConical className="w-4 h-4 shrink-0 text-violet-700" aria-hidden />
        <span className="min-w-0">{t("banner")}</span>
        <button ref={trigger} type="button" onClick={() => setOpen(true)} className="font-medium underline underline-offset-2 hover:no-underline focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded">
          {t("delete")}
        </button>
      </div>
      {blockers.length > 0 ? (
        <div role="alert" className="px-4 sm:px-6 lg:px-8 pb-3 text-sm text-violet-950">
          <p className="font-medium">{t("blockedTitle")}</p>
          <ul className="mt-1 list-disc pl-5 space-y-0.5">
            {blockers.map((b, i) => (
              <li key={`${b.field}-${b.code}-${i}`}>{t.has(`blocker.${b.code}`) ? t(`blocker.${b.code}`, { ref: b.field }) : t("blocker.other", { ref: b.field })}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <ConfirmDialog
        open={open}
        title={t("confirmTitle")}
        body={t("confirmBody")}
        confirmLabel={pending ? t("deleting") : t("confirm")}
        cancelLabel={t("cancel")}
        pending={pending}
        returnFocusRef={trigger}
        onCancel={() => setOpen(false)}
        onConfirm={() =>
          start(async () => {
            const r = await deleteSampleData();
            setOpen(false);
            if (!r.ok) {
              setBlockers(r.blockers);
              return void toast.error(r.error);
            }
            setBlockers([]);
            toast.success(t("deleted", { count: r.deleted }));
            router.refresh();
          })
        }
      />
    </section>
  );
}
