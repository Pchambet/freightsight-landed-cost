"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { btnSecondary, input, td, tdRight, th, thRight } from "@/components/layout/AppShell";
import ScrollRegion from "@/components/shared/ScrollRegion";
import type { Schemas } from "@/lib/api/client";
import { unsigned } from "@/features/imports/IssueGroups";
import { COST_TYPES, type CostType } from "@/lib/domain";
import { dateShort, money } from "@/lib/format";

type Report = Schemas["ImportReport"];

/**
 * A costs file's preview says the money before anything else — what would enter the landed costs, by
 * type and by currency, what it replaces, what it leaves out because it is already in the books —
 * then asks for what it could not type: one choice per wording (« FACT TRANSDEMO », « 6241 ») rather
 * than one per line. A mixed wording (« débours », « droits et taxes ») is never offered here: its
 * rows are errors that say to split the line, since import VAT must never enter a landed cost.
 */
export default function CostsPreview({
  report,
  baseCurrency,
  labelTypes,
  pending,
  onRetype,
}: {
  report: Report;
  baseCurrency: string;
  labelTypes: Record<string, CostType>;
  pending: boolean;
  onRetype: (next: Record<string, CostType>) => void;
}) {
  const t = useTranslations("importWizard.costs");
  const costType = useTranslations("domain.costType");
  const locale = useLocale();
  const eur = (v: string | null | undefined) => money(v, baseCurrency, locale);
  const [draft, setDraft] = useState<Record<string, CostType | "">>({});
  const m = report.money;
  const unknown = report.unknown_labels ?? [];
  const credits = report.credit_notes_skipped ?? [];
  // Only the wordings this preview still asks about: one typed by the last round is no longer offered.
  const offered = new Set(unknown.map((u) => u.label));
  const chosen = Object.fromEntries(Object.entries(draft).filter(([k, v]) => v && offered.has(k))) as Record<string, CostType>;

  return (
    <div className="space-y-5">
      {m ? (
        <div>
          <p className="text-sm text-slate-800">
            <span className="text-lg font-semibold tabular-nums">{eur(m.total_base)}</span> {t("total")}
          </p>
          <ul className="mt-1 text-xs text-slate-600 space-y-0.5">
            {report.estimates_replaced ? <li>{t("estimatesReplaced", { count: report.estimates_replaced, amount: eur(report.estimates_replaced_base) })}</li> : null}
            {report.duplicates ? <li>{t("duplicates", { count: report.duplicates, amount: eur(report.duplicates_base) })}</li> : null}
            {report.reversed_in_file ? <li>{t("reversed", { count: report.reversed_in_file })}</li> : null}
          </ul>
          {m.by_type?.length ? (
            <ScrollRegion label={t("byType")} className="mt-3 rounded-md border border-slate-200 max-w-2xl">
              <table className="min-w-full divide-y divide-slate-200 text-sm">
                <caption className="sr-only">{t("byType")}</caption>
                <thead className="bg-slate-50"><tr><th className={th}>{t("th.type")}</th><th className={thRight}>{t("th.rows")}</th><th className={thRight}>{t("th.amount", { currency: baseCurrency })}</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {m.by_type.map((r) =>
                    // Import VAT is tracked, never a landed cost: listed, outside the total above.
                    r.cost_type === "IMPORT_VAT" ? (
                      <tr key={r.cost_type} className="text-slate-500"><td className={td}>{costType(r.cost_type)}<span className="text-xs"> — {t("vatApart")}</span></td><td className={tdRight}>{r.rows}</td><td className={tdRight}>{eur(r.total_base)}</td></tr>
                    ) : (
                      <tr key={r.cost_type}><td className={td}>{costType(r.cost_type)}</td><td className={tdRight}>{r.rows}</td><td className={tdRight}>{eur(r.total_base)}</td></tr>
                    ),
                  )}
                </tbody>
              </table>
            </ScrollRegion>
          ) : null}
          {(m.by_currency?.length ?? 0) > 1 ? (
            <ScrollRegion label={t("byCurrency")} className="mt-3 rounded-md border border-slate-200 max-w-2xl">
              <table className="min-w-full divide-y divide-slate-200 text-sm">
                <caption className="sr-only">{t("byCurrency")}</caption>
                <thead className="bg-slate-50"><tr><th className={th}>{t("th.currency")}</th><th className={thRight}>{t("th.rows")}</th><th className={thRight}>{t("th.inCurrency")}</th><th className={thRight}>{t("th.amount", { currency: baseCurrency })}</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {m.by_currency!.map((r) => (
                    <tr key={r.currency}><td className={td}>{r.currency}</td><td className={tdRight}>{r.rows}</td><td className={tdRight}>{money(r.total, r.currency, locale)}</td><td className={tdRight}>{eur(r.total_base)}</td></tr>
                  ))}
                </tbody>
              </table>
            </ScrollRegion>
          ) : null}
        </div>
      ) : null}

      {unknown.length ? (
        <div>
          <h3 className="text-sm font-semibold text-slate-900">{t("labelsTitle", { count: unknown.length })}</h3>
          <p className="mt-1 text-xs text-slate-600 max-w-2xl">{t("labelsHint")}</p>
          <ScrollRegion label={t("labelsTitle", { count: unknown.length })} className="mt-2 rounded-md border border-slate-200 max-w-3xl max-h-80 overflow-y-auto">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50"><tr><th className={th}>{t("th.label")}</th><th className={thRight}>{t("th.rows")}</th><th className={thRight}>{t("th.amount", { currency: baseCurrency })}</th><th className={th}>{t("th.type")}</th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {unknown.map((u, i) => (
                  <tr key={u.label}>
                    <td className={`${td} font-mono text-xs`}>{u.label}</td>
                    <td className={tdRight}>{u.rows}</td>
                    <td className={tdRight}>{eur(u.total_base)}</td>
                    <td className="px-4 py-1.5">
                      <select
                        aria-label={t("typeFor", { label: u.label })}
                        id={`label-type-${i}`}
                        value={draft[u.label] ?? ""}
                        onChange={(e) => setDraft((d) => ({ ...d, [u.label]: e.target.value as CostType | "" }))}
                        className={`${input} !py-1`}
                      >
                        <option value="">{t("chooseType")}</option>
                        {COST_TYPES.map((c) => <option key={c} value={c}>{costType(c)}</option>)}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollRegion>
          <button
            type="button"
            disabled={pending || Object.keys(chosen).length === 0}
            onClick={() => onRetype({ ...labelTypes, ...chosen })}
            className={`${btnSecondary} mt-2`}
          >
            {t("retype", { count: Object.keys(chosen).length })}
          </button>
        </div>
      ) : null}

      {credits.length ? (
        <div>
          <h3 className="text-sm font-semibold text-slate-900">{t("creditsTitle", { count: credits.length, amount: eur(unsigned(report.credit_notes_skipped_base ?? "0")) })}</h3>
          <p className="mt-1 text-xs text-slate-600 max-w-2xl">{t("creditsHint")}</p>
          <ul className="mt-2 text-xs text-slate-700 space-y-0.5">
            {credits.slice(0, 12).map((c) => (
              <li key={c.row} className="tabular-nums">
                {t("creditLine", { row: c.row, date: c.cost_date ? dateShort(c.cost_date, locale) : "—", vendor: c.vendor || "—", invoice: c.invoice_number || "—", amount: money(unsigned(c.amount), c.currency, locale) })}
                {c.reverses_cost_id ? <span className="font-medium text-amber-800"> · {t("reversesCost")}</span> : null}
              </li>
            ))}
            {credits.length > 12 ? <li>{t("creditsMore", { count: credits.length - 12 })}</li> : null}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
