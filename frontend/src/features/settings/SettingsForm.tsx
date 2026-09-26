"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { Card, btnPrimary, input, label } from "@/components/layout/AppShell";
import { updateOrganization } from "@/features/actions";
import type { OrganizationResponse } from "@/lib/api/client";
import { USER_METHODS } from "@/lib/domain";

export default function SettingsForm({ org }: { org: OrganizationResponse }) {
  const t = useTranslations("settings");
  const tt = useTranslations("tracking");
  const methodLabel = useTranslations("domain.method");
  const [pending, startTransition] = useTransition();
  const router = useRouter();

  const submit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    startTransition(async () => {
      const result = await updateOrganization({
        name: String(f.get("name") ?? org.name),
        base_currency: String(f.get("base_currency") ?? org.base_currency).toUpperCase(),
        locale: f.get("locale") === "en" ? "en" : "fr",
        default_allocation_method: String(f.get("default_allocation_method") ?? org.default_allocation_method),
        free_days_demurrage: Number(f.get("free_days_demurrage") ?? org.free_days_demurrage),
        free_days_detention: Number(f.get("free_days_detention") ?? org.free_days_detention),
        settings: { ...(org.settings ?? {}), tracking_provider_default: String(f.get("tracking_provider_default") ?? "manual") },
      });
      if (!result.ok) return void toast.error(result.error);
      toast.success(t("saved"));
      router.refresh();
    });
  };

  return (
    <form onSubmit={submit} className="space-y-6 max-w-2xl">
      <Card title={t("organization")}>
        <div className="p-5 grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="sm:col-span-2">
            <label className={label} htmlFor="name">{t("name")}</label>
            <input id="name" name="name" defaultValue={org.name} required className={input} />
          </div>
          <div>
            <label className={label} htmlFor="base_currency">{t("baseCurrency")}</label>
            <input id="base_currency" name="base_currency" defaultValue={org.base_currency} maxLength={3} className={`${input} font-mono uppercase`} />
            <p className="text-xs text-slate-500 mt-1">{t("baseCurrencyHelp")}</p>
          </div>
          <div>
            <label className={label} htmlFor="locale">{t("locale")}</label>
            <select id="locale" name="locale" defaultValue={org.locale} className={input}>
              <option value="fr">{t("locale_fr")}</option>
              <option value="en">{t("locale_en")}</option>
            </select>
            <p className="text-xs text-slate-500 mt-1">{t("localeHelp")}</p>
          </div>
        </div>
      </Card>
      <Card title={t("allocation")}>
        <div className="p-5 space-y-3">
          <div>
            <label className={label} htmlFor="default_allocation_method">{t("defaultMethod")}</label>
            <select id="default_allocation_method" name="default_allocation_method" defaultValue={org.default_allocation_method} className={input}>
              {USER_METHODS.map((m) => <option key={m} value={m}>{methodLabel(m)}</option>)}
            </select>
          </div>
          <p className="text-xs text-slate-500">{t("methodHelp")}</p>
        </div>
      </Card>
      <Card title={t("freeTime")}>
        <div className="p-5 grid grid-cols-2 gap-4">
          <div>
            <label className={label} htmlFor="free_days_demurrage">{t("demurrageDays")}</label>
            <input id="free_days_demurrage" name="free_days_demurrage" type="number" min={0} max={60} defaultValue={org.free_days_demurrage} className={input} />
          </div>
          <div>
            <label className={label} htmlFor="free_days_detention">{t("detentionDays")}</label>
            <input id="free_days_detention" name="free_days_detention" type="number" min={0} max={60} defaultValue={org.free_days_detention} className={input} />
          </div>
          <p className="col-span-2 text-xs text-slate-500">{t("freeTimeHelp")}</p>
        </div>
      </Card>
      <Card title={tt("providerTitle")}>
        <div className="p-5 space-y-2">
          <label className={label} htmlFor="tracking_provider_default">{tt("providerDefault")}</label>
          <select id="tracking_provider_default" name="tracking_provider_default" defaultValue={String((org.settings as Record<string, unknown> | undefined)?.tracking_provider_default ?? "manual")} className={input}>
            {(["manual", "terminal49", "shipsgo"] as const).map((p) => <option key={p} value={p}>{tt(`provider.${p}`)}</option>)}
          </select>
          <p className="text-xs text-slate-500">{tt("providerHelp")}</p>
        </div>
      </Card>
      <button type="submit" disabled={pending} className={btnPrimary}>{t("save")}</button>
    </form>
  );
}
