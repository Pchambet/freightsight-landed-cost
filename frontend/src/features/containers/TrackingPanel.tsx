"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";
import { Card, btnSecondary, input, label } from "@/components/layout/AppShell";
import { subscribeTracking, unsubscribeTracking, updateContainer } from "@/features/actions";
import type { ContainerResponse, Schemas } from "@/lib/api/client";
import { MILESTONES, type ContainerMilestone } from "@/lib/domain";
import { containerTypeOptions } from "@/lib/containerTypes";
import { dateShort } from "@/lib/format";

function toDateInput(iso: string | null): string {
  return iso ? iso.slice(0, 10) : "";
}

export default function TrackingPanel({ container, subscription }: { container: ContainerResponse; subscription: Schemas["TrackingSubscriptionResponse"] | null }) {
  const t = useTranslations("tracking");
  const milestoneLabel = useTranslations("domain.milestone");
  const typeLabel = useTranslations("domain.containerType");
  const locale = useLocale();
  const [milestone, setMilestone] = useState<ContainerMilestone>(container.milestone);
  const [discharged, setDischarged] = useState(toDateInput(container.discharged_at));
  const [freeDays, setFreeDays] = useState(container.free_days_demurrage?.toString() ?? "");
  const [isoType, setIsoType] = useState(container.iso_type ?? "");
  const [pending, startTransition] = useTransition();
  const router = useRouter();

  const save = () =>
    startTransition(async () => {
      const result = await updateContainer(container.id, {
        milestone,
        discharged_at: discharged ? `${discharged}T12:00:00Z` : null,
        free_days_demurrage: freeDays === "" ? null : Number(freeDays),
        iso_type: isoType || null,
      });
      if (!result.ok) return void toast.error(result.error);
      toast.success(t("saved"));
      router.refresh();
    });

  const subscribe = () =>
    startTransition(async () => {
      const r = subscription ? await unsubscribeTracking(container.id) : await subscribeTracking(container.id);
      if (!r.ok) return void toast.error(r.error);
      toast.success(subscription ? t("unsubscribed") : t("subscribeDone"));
      router.refresh();
    });
  const providerLabel = (p: string) => (t.has(`provider.${p}`) ? t(`provider.${p}`) : p);

  return (
    <Card title={t("title")} right={<span className="text-xs text-slate-500">{subscription ? `${t("subscribed", { provider: providerLabel(subscription.provider) })} · ${t("subStatus", { status: subscription.status })}` : t("manualNote")}</span>}>
      <div className="p-5 space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={label} htmlFor="milestone">{t("milestone")}</label>
            <select id="milestone" value={milestone} onChange={(e) => setMilestone(e.target.value as ContainerMilestone)} className={input}>
              {MILESTONES.map((m) => <option key={m} value={m}>{milestoneLabel(m)}</option>)}
            </select>
          </div>
          <div>
            <label className={label} htmlFor="iso-type">{t("containerType")}</label>
            <select id="iso-type" value={isoType} onChange={(e) => setIsoType(e.target.value)} aria-describedby={isoType ? undefined : "iso-type-hint"} className={input}>
              <option value="">{t("containerTypeUnset")}</option>
              {containerTypeOptions(container.iso_type).map((c) => <option key={c} value={c}>{typeLabel.has(c as never) ? typeLabel(c as never) : c}</option>)}
            </select>
          </div>
        </div>
        {!isoType ? <p id="iso-type-hint" className="-mt-2 text-xs text-slate-500">{t("containerTypeHint")}</p> : null}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={label} htmlFor="discharged">{t("dischargedOn")}</label>
            <input id="discharged" type="date" value={discharged} onChange={(e) => setDischarged(e.target.value)} className={input} />
          </div>
          <div>
            <label className={label} htmlFor="freedays">{t("freeDays")}</label>
            <input id="freedays" type="number" min={0} max={60} placeholder={t("freeDaysPlaceholder")} value={freeDays} onChange={(e) => setFreeDays(e.target.value)} className={input} />
          </div>
        </div>
        <dl className="text-xs text-slate-500 grid grid-cols-2 gap-x-3 gap-y-1">
          <dt>{t("eta")}</dt><dd className="text-slate-700">{dateShort(container.eta, locale)}</dd>
          <dt>{t("arrived")}</dt><dd className="text-slate-700">{dateShort(container.ata, locale)}</dd>
          <dt>{t("gateOut")}</dt><dd className="text-slate-700">{dateShort(container.gate_out_at, locale)}</dd>
          <dt>{t("lastFreeDay")}</dt><dd className="text-slate-700">{dateShort(container.last_free_day, locale)}</dd>
        </dl>
        <div className="flex flex-wrap gap-2 items-center">
          <button type="button" onClick={save} disabled={pending} className={btnSecondary}>{t("save")}</button>
          <button type="button" onClick={subscribe} disabled={pending} className="text-sm text-blue-600 hover:underline px-2">{subscription ? t("unsubscribe") : t("subscribe")}</button>
        </div>
        {/* subscription.last_error is a raw provider string (Terminal49/Shipsgo failure detail, sometimes with an
            internal hostname) — never shown verbatim; see needs_other_lane for a coded field to replace it. */}
        {subscription?.last_error ? <p className="text-xs text-red-600">{t("lastAttemptFailed")}</p> : null}
      </div>
    </Card>
  );
}
