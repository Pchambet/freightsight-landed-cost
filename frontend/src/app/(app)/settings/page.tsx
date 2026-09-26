import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { PageHeader } from "@/components/layout/AppShell";
import CollapsibleSection from "@/components/shared/CollapsibleSection";
import SettingsForm from "@/features/settings/SettingsForm";
import RateCards from "@/features/settings/RateCards";
import ServiceStatus from "@/features/settings/ServiceStatus";
import ErpConnection from "@/features/erp/ErpConnection";
import WorkflowNextStep from "@/features/workflow/WorkflowNextStep";
import { settingsHubStep } from "@/features/workflow/steps";
import { api } from "@/lib/api/client";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("settings");
  return { title: t("title") };
}

export default async function SettingsPage() {
  const [{ data, error }, cards, erp, runs, service, t] = await Promise.all([
    api.GET("/api/v1/organization"),
    api.GET("/api/v1/organization/rate-cards"),
    api.GET("/api/v1/erp/connection"),
    api.GET("/api/v1/erp/sync-runs", { params: { query: { limit: 10 } } }),
    api.GET("/api/v1/service-status"),
    getTranslations("settings"),
  ]);
  if (error || !data) throw new Error("api_error");
  const rateCards = cards.data ?? [];
  const hubStep = settingsHubStep(rateCards.length > 0);

  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <WorkflowNextStep step={hubStep} />
      <div className="space-y-3">
        <CollapsibleSection id="organization" title={t("sections.organization")} hint={t("sections.organizationHint")} defaultOpen>
          <div className="p-5">
            <SettingsForm org={data} />
          </div>
        </CollapsibleSection>
        <CollapsibleSection id="rate-cards" title={t("sections.rateCards")} hint={t("sections.rateCardsHint")} defaultOpen={rateCards.length === 0}>
          <div className="p-5">
            <RateCards cards={rateCards} currency={data.base_currency} />
          </div>
        </CollapsibleSection>
        <CollapsibleSection id="erp" title={t("sections.erp")} hint={t("sections.erpHint")}>
          <div className="p-5">
            <ErpConnection connection={erp.response.status === 200 ? (erp.data ?? null) : null} runs={runs.data ?? []} />
          </div>
        </CollapsibleSection>
        {service.data ? (
          <CollapsibleSection id="service" title={t("sections.service")} hint={t("sections.serviceHint")} defaultOpen={service.data.checks.some((c) => c.state === "degraded")}>
            <ServiceStatus checks={service.data.checks} checkedAt={service.data.checked_at} />
          </CollapsibleSection>
        ) : null}
      </div>
    </>
  );
}
