import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Ship } from "lucide-react";
import { getTranslations } from "next-intl/server";
import AuditSheet from "@/features/report/AuditSheet";
import LandedCostSheet from "@/features/report/LandedCostSheet";
import PrintButton from "@/features/report/PrintButton";
import type { Schemas } from "@/lib/api/client";
import { env } from "@/lib/env";
import { site } from "@/lib/site";

type Shared = Schemas["SharedReportPublic"];
type Params = { params: Promise<{ token: string }> };

// A private document behind an unguessable link: never indexed, never cached, never previewed.
// `absolute`: the root layout's "%s · FreightSight" template made it "FreightSight · FreightSight". The
// title stays neutral on purpose — no company, no period — because a chat that unfurls the link shows
// it to everyone in the channel, and reading the share here would count one more opening.
export const metadata: Metadata = { title: { absolute: "FreightSight" }, robots: { index: false, follow: false, nocache: true }, referrer: "no-referrer" };

/**
 * No session, no tenant header, nothing of the visitor: the token is the whole credential, and the
 * API answers the same 404 for a link that never existed, has expired or was withdrawn.
 */
async function load(token: string): Promise<Shared | "busy" | null> {
  if (!/^[A-Za-z0-9_-]{20,200}$/.test(token)) return null;
  try {
    // The token goes in the BODY: a path or a query string ends up in access logs and error
    // reports, a body does not.
    const res = await fetch(`${env.API_URL}/api/v1/public/shared-reports/open`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token }),
      cache: "no-store",
      signal: AbortSignal.timeout(20_000),
    });
    if (res.status === 429) return "busy";
    return res.ok ? ((await res.json()) as Shared) : null;
  } catch {
    return null;
  }
}

export default async function SharedReportPage({ params }: Params) {
  const { token } = await params;
  const shared = await load(token);
  // A snapshot carries exactly one of the two documents; one that carries neither is no document.
  if (!shared || (shared !== "busy" && !shared.report && !shared.audit)) notFound();
  if (shared === "busy") {
    const tb = await getTranslations("sheet.busy");
    return (
      <div className="min-h-screen bg-slate-100 px-4 py-16">
        <div className="mx-auto max-w-md rounded-xl border border-slate-200 bg-white shadow-card px-6 py-10 text-center">
          <h1 className="text-lg font-semibold text-slate-900">{tb("title")}</h1>
          <p className="mt-2 text-sm text-slate-600">{tb("body")}</p>
        </div>
      </div>
    );
  }
  const locale = shared.locale === "en" ? "en" : "fr";
  const t = await getTranslations({ locale, namespace: "sheet" });

  return (
    <div lang={locale} className="min-h-screen bg-slate-100 print:bg-white">
      <header className="print:hidden border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-[52rem] px-4 h-14 flex items-center justify-between gap-3">
          <a href={site.url} className="flex items-center gap-2" rel="noopener noreferrer" referrerPolicy="no-referrer">
            <span className="bg-blue-600 p-1.5 rounded-md"><Ship className="w-4 h-4 text-white" aria-hidden /></span>
            <span className="text-lg font-semibold tracking-tight">Freight<span className="text-blue-600">Sight</span></span>
          </a>
          <PrintButton label={t("actions.print")} />
        </div>
      </header>
      <main className="px-4 py-8 print:p-0">
        {shared.subject_type === "audit" && shared.audit ? (
          <AuditSheet
            locale={locale}
            audit={shared.audit}
            header={{ issuer: shared.issuer.name, generatedAt: shared.generated_at, expiresAt: shared.expires_at, sampleData: shared.sample_data, redacted: shared.redacted }}
          />
        ) : shared.report ? (
          <LandedCostSheet
            locale={locale}
            report={shared.report}
            header={{
              issuer: shared.issuer.name,
              subjectType: shared.subject_type === "purchase_order" ? "purchase_order" : "container",
              subjectLabel: shared.subject_label,
              shipmentReference: shared.shipment_reference,
              generatedAt: shared.generated_at,
              expiresAt: shared.expires_at,
              sampleData: shared.sample_data,
              redacted: shared.redacted,
            }}
          />
        ) : null}
      </main>
    </div>
  );
}
