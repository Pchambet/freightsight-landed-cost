"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { btnSecondary } from "@/components/layout/AppShell";
import { markAlertRead, markAllAlertsRead } from "@/features/actions";

export function MarkReadButton({ alertId }: { alertId: string }) {
  const t = useTranslations("alerts");
  const [pending, start] = useTransition();
  const router = useRouter();
  return (
    <button
      type="button"
      disabled={pending}
      onClick={() => start(async () => { const r = await markAlertRead(alertId); if (!r.ok) return void toast.error(r.error); router.refresh(); })}
      className="text-xs text-blue-600 hover:underline disabled:opacity-50 whitespace-nowrap"
    >
      {t("markRead")}
    </button>
  );
}

export function MarkAllReadButton() {
  const t = useTranslations("alerts");
  const [pending, start] = useTransition();
  const router = useRouter();
  return (
    <button
      type="button"
      disabled={pending}
      onClick={() => start(async () => { const r = await markAllAlertsRead(); if (!r.ok) return void toast.error(r.error); toast.success(t("allMarked", { count: r.data.marked })); router.refresh(); })}
      className={btnSecondary}
    >
      {t("markAllRead")}
    </button>
  );
}
