"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { CheckCheck } from "lucide-react";
import { toast } from "sonner";
import { btnPrimary, input, label } from "@/components/layout/AppShell";
import { acknowledgePeriodDrift } from "@/features/actions";

/**
 * "This gap is in my books now." A closed month keeps asking as long as costs that landed after the
 * close have not been posted somewhere; once the accountant has passed the adjustment, one click and
 * an optional note (the journal entry's reference) quiets the month — until a new cost moves it again.
 */
export default function AcknowledgeDrift({ period, outstanding }: { period: string; outstanding: string }) {
  const t = useTranslations("periods.ack");
  const router = useRouter();
  const [note, setNote] = useState("");
  const [pending, start] = useTransition();
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        start(async () => {
          const r = await acknowledgePeriodDrift(period, note);
          if (!r.ok) return void toast.error(r.error);
          toast.success(t("done"));
          setNote("");
          router.refresh();
        });
      }}
    >
      <div className="flex-1 min-w-52">
        <label className={label} htmlFor="ack-note">{t("note")}</label>
        <input id="ack-note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder={t("notePlaceholder")} className={input} />
      </div>
      <button type="submit" disabled={pending} className={btnPrimary}>
        <CheckCheck className="w-4 h-4 mr-1.5" aria-hidden />
        {pending ? t("working") : t("action", { amount: outstanding })}
      </button>
    </form>
  );
}
