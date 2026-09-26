import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { CheckCircle2, Circle } from "lucide-react";
import type { ContainerSummary } from "@/lib/api/client";

type Step = { key: string; done: boolean; href: string; label: string };

export default async function OnboardingChecklist({
  containers,
  purchaseOrderCount,
  confirmedInvoiceCount,
}: {
  containers: ContainerSummary[];
  purchaseOrderCount: number;
  confirmedInvoiceCount: number;
}) {
  const t = await getTranslations("onboarding");
  const withLoads = containers.find((c) => c.load_count > 0);
  const withCosts = containers.find((c) => (c.cost_types_present?.length ?? 0) > 0);
  const withUnitCosts = containers.find((c) => c.load_count > 0 && Number(c.allocated_base) > 0);

  const steps: Step[] = [
    { key: "import", done: purchaseOrderCount > 0, href: "/imports/new", label: t("steps.import") },
    { key: "loads", done: !!withLoads, href: withLoads ? `/container/${withLoads.id}#chargements` : "/containers", label: t("steps.loads") },
    { key: "costs", done: !!withCosts, href: "/invoices", label: t("steps.costs") },
    { key: "invoice", done: confirmedInvoiceCount > 0, href: "/invoices", label: t("steps.invoice") },
    {
      key: "unitCosts",
      done: !!withUnitCosts,
      href: withUnitCosts ? `/container/${withUnitCosts.id}#couts-unitaires` : "/containers",
      label: t("steps.unitCosts"),
    },
  ];

  if (steps.every((s) => s.done)) return null;

  const doneCount = steps.filter((s) => s.done).length;
  const next = steps.find((s) => !s.done)!;

  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-4 mb-6">
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-blue-800">{t("title")}</p>
          <p className="text-sm text-blue-950 mt-1">{t("subtitle", { done: doneCount, total: steps.length })}</p>
        </div>
        <Link
          href={next.href}
          className="inline-flex items-center justify-center bg-slate-900 hover:bg-slate-800 text-white text-sm font-medium py-2 px-4 rounded-md shrink-0"
        >
          {t("continue")} →
        </Link>
      </div>
      <ol className="mt-4 grid gap-2 sm:grid-cols-2">
        {steps.map((s) => (
          <li key={s.key}>
            <Link
              href={s.href}
              aria-current={s.key === next.key ? "step" : undefined}
              className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors ${
                s.done ? "text-emerald-800 bg-emerald-50/80" : s.key === next.key ? "text-blue-950 bg-white/80 ring-1 ring-blue-200" : "text-slate-600 hover:bg-white/60"
              }`}
            >
              {s.done ? <CheckCircle2 className="w-4 h-4 shrink-0 text-emerald-600" aria-hidden /> : <Circle className="w-4 h-4 shrink-0 text-slate-500" aria-hidden />}
              <span>{s.label}</span>
              {/* The icon and the colours say it to the eye; this says it to a screen reader. */}
              <span className="sr-only">{s.done ? t("stepDone") : t("stepTodo")}</span>
            </Link>
          </li>
        ))}
      </ol>
    </div>
  );
}
