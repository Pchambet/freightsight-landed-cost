import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { PageHeader } from "@/components/layout/AppShell";
import Breadcrumb from "@/components/shared/Breadcrumb";
import ApiErrorNotice from "@/components/shared/ApiErrorNotice";
import ImportWizard from "@/features/imports/ImportWizard";
import { api } from "@/lib/api/client";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("importWizard");
  return { title: t("title") };
}

export default async function NewImportPage({ searchParams }: { searchParams: Promise<{ job?: string; kind?: string }> }) {
  const [{ job, kind }, t, ti, kindsRes, org] = await Promise.all([
    searchParams,
    getTranslations("importWizard"),
    getTranslations("imports"),
    api.GET("/api/v1/imports/kinds"),
    api.GET("/api/v1/organization"),
  ]);
  const kinds = kindsRes.data ?? [];
  // A remedy link (« importez d'abord le suivi des conteneurs ») opens the wizard on that kind.
  const initialKind = kinds.find((k) => k.kind === kind)?.kind ?? null;
  let initial = null;
  if (job) {
    const { data } = await api.GET("/api/v1/imports/{import_id}", { params: { path: { import_id: job } } });
    if (data && data.status !== "DONE") initial = data;
  }
  return (
    <>
      {/* The title right below already says what the page is — the breadcrumb only needs the back link (audit B21). */}
      <Breadcrumb backHref="/imports" backLabel={ti("title")} />
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      {/* The next-step card lives inside the wizard now: it has to follow the live client step (audit B20), which this server page cannot see. */}
      <div id="wizard" className="scroll-mt-6">
        {kinds.length ? (
          <ImportWizard kinds={kinds} initialJob={initial} initialKind={initialKind} baseCurrency={org.data?.base_currency ?? "EUR"} />
        ) : (
          <ApiErrorNotice error={kindsRes.error} retryHref="/imports/new" />
        )}
      </div>
    </>
  );
}
