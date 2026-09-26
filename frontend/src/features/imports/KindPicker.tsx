"use client";

import { useLocale, useTranslations } from "next-intl";
import { Download } from "lucide-react";
import { label } from "@/components/layout/AppShell";
import type { Schemas } from "@/lib/api/client";
import type { ImportKind } from "@/lib/domain";
import { downloadImportTemplate } from "@/features/imports/importTemplate";

type KindInfo = Schemas["ImportKindInfo"];

/**
 * The four files of a quarter, in the order the API gives them — the order they build on each other:
 * the price list lends its duty rates only to order lines written after it, costs land on the boxes
 * and orders already there. Each card says what the file brings to the audit and where an importer
 * finds it at home, with an empty template of the columns it reads. The old one-row-per-order format
 * stays reachable, folded away.
 */
export default function KindPicker({ kinds, value, onChange }: { kinds: KindInfo[]; value: ImportKind; onChange: (kind: ImportKind) => void }) {
  const t = useTranslations("importWizard.kinds");
  const locale = useLocale();
  const main = kinds.filter((k) => k.kind !== "LEGACY_PO_CONTAINER");
  const legacy = kinds.find((k) => k.kind === "LEGACY_PO_CONTAINER");
  const card = (k: KindInfo, n: number | null) => (
    <div
      key={k.kind}
      className={`flex flex-col rounded-lg border p-4 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-blue-500 ${
        value === k.kind ? "border-blue-500 bg-blue-50/50" : "border-slate-200 hover:bg-slate-50"
      }`}
    >
      <label className="flex flex-col gap-1 cursor-pointer">
        <input type="radio" name="import-kind" value={k.kind} checked={value === k.kind} onChange={() => onChange(k.kind)} className="sr-only" />
        <span className="flex items-baseline gap-2">
          {n !== null ? <span className="text-xs font-medium tabular-nums text-slate-500">{n}</span> : null}
          <span className="text-sm font-semibold text-slate-900">{t(`${k.kind}.title`)}</span>
        </span>
        <span className="text-sm text-slate-700">{t(`${k.kind}.brings`)}</span>
        <span className="text-xs text-slate-500">{t(`${k.kind}.where`)}</span>
      </label>
      <button
        type="button"
        onClick={() => downloadImportTemplate(k, locale)}
        aria-label={t("templateFor", { kind: t(`${k.kind}.title`) })}
        className="mt-3 inline-flex items-center gap-1.5 self-start text-xs text-blue-700 underline underline-offset-2 hover:no-underline"
      >
        <Download className="w-3.5 h-3.5" aria-hidden />
        {t("template")}
      </button>
    </div>
  );
  return (
    <fieldset>
      <legend className={label}>{t("legend")}</legend>
      <p className="mb-3 text-xs text-slate-500">{t("order")}</p>
      <div className="grid gap-3 sm:grid-cols-2">{main.map((k, i) => card(k, i + 1))}</div>
      {legacy ? (
        <details className="mt-3" open={value === legacy.kind}>
          <summary className="cursor-pointer text-xs text-slate-600">{t("other")}</summary>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">{card(legacy, null)}</div>
        </details>
      ) : null}
    </fieldset>
  );
}
