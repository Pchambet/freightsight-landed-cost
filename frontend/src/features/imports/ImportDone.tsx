"use client";

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { btnPrimary, btnSecondary } from "@/components/layout/AppShell";
import UndoImportButton from "@/features/imports/UndoImportButton";
import type { ImportJobResponse } from "@/lib/api/client";
import { NEXT_IMPORT_KIND, type ImportKind } from "@/lib/domain";
import { money } from "@/lib/format";

/**
 * What the import unlocked, in words — « 23 conteneurs datés », « 212 coûts créés pour 48 310,00 € » —
 * then where to go: the next file of the quarter, the audit's preparation list, and, for a costs file,
 * the way back.
 */
export default function ImportDone({ job, baseCurrency, onAnother }: { job: ImportJobResponse; baseCurrency: string; onAnother: (next?: ImportKind) => void }) {
  const t = useTranslations("importWizard.done");
  const tk = useTranslations("importWizard.kinds");
  const locale = useLocale();
  const r = job.report;
  const skipped = new Set((r.errors ?? []).map((e) => e.row)).size;
  const lines: string[] = [];
  switch (job.kind) {
    case "PRODUCTS":
      lines.push(t("products", { created: r.products_created ?? 0, updated: r.products_updated ?? 0 }));
      break;
    case "CONTAINERS":
      lines.push(t("containers", { created: r.containers_created ?? 0, updated: r.containers_updated ?? 0, dated: r.containers_dated ?? 0 }));
      lines.push(t("shipments", { shipments: r.shipments_created ?? 0, loads: r.loads_created ?? 0 }));
      if (r.invoices_matched) lines.push(t("invoicesMatched", { count: r.invoices_matched }));
      if (r.po_split) lines.push(t("poSplit", { count: r.po_split }));
      break;
    case "COSTS": {
      // The total leaves out the import VAT the file brought: tracked, never a landed cost.
      lines.push(t("costs", { created: r.costs_created ?? 0, amount: money(r.money?.total_base ?? "0", baseCurrency, locale), vat: r.money?.by_type?.some((x) => x.cost_type === "IMPORT_VAT") ? "yes" : "no" }));
      if (r.costs_skipped) lines.push(t("costsSkipped", { count: r.costs_skipped }));
      if (r.estimates_replaced) lines.push(t("estimatesReplaced", { count: r.estimates_replaced }));
      // A credit note that cancels a cost still counted asks for that cost to go; the others only for a look.
      const credits = r.credit_notes_skipped ?? [];
      const reversing = credits.filter((c) => c.reverses_cost_id).length;
      if (reversing) lines.push(t("creditReverses", { count: reversing }));
      if (credits.length > reversing) lines.push(t("creditNotes", { count: credits.length - reversing }));
      break;
    }
    default:
      lines.push(t("summary", { pos: r.purchase_orders_created ?? 0, lines: r.lines_created ?? 0, containers: r.containers_created ?? 0, loads: r.loads_created ?? 0 }));
  }
  if (skipped) lines.push(t("skipped", { rows: skipped }));
  const next = NEXT_IMPORT_KIND[job.kind];
  return (
    <div className="p-5 space-y-4 text-sm">
      <ul className="space-y-1 text-slate-800">
        {lines.map((l) => <li key={l}>{l}</li>)}
      </ul>
      <div className="flex flex-wrap items-center gap-2">
        {next ? (
          <button type="button" onClick={() => onAnother(next)} className={btnPrimary}>{t("next", { kind: tk(`${next}.title`) })}</button>
        ) : null}
        <Link href="/cost-audit" className={next ? btnSecondary : btnPrimary}>{t("prepareAudit")}</Link>
        <button type="button" onClick={() => onAnother()} className={btnSecondary}>{t("another")}</button>
        {job.kind === "COSTS" && job.can_undo ? <UndoImportButton importId={job.id} costCount={r.costs_created ?? 0} estimatesReplaced={r.estimates_replaced ?? 0} onUndone={() => onAnother()} /> : null}
      </div>
    </div>
  );
}
