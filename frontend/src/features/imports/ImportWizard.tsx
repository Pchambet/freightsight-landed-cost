"use client";

import { useMemo, useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { CheckCircle2, FileType, FileWarning, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Card, btnPrimary, btnSecondary, input, label } from "@/components/layout/AppShell";
import FileTooLarge from "@/components/shared/FileTooLarge";
import NextStepCard from "@/components/shared/NextStepCard";
import { commitImport, createImport, validateImport } from "@/features/actions";
import CostsPreview from "@/features/imports/CostsPreview";
import ImportDone from "@/features/imports/ImportDone";
import IssueGroups, { useIssueSentence } from "@/features/imports/IssueGroups";
import KindPicker from "@/features/imports/KindPicker";
import { importWizardStep } from "@/features/workflow/steps";
import type { ImportJobResponse, Schemas } from "@/lib/api/client";
import { COST_TYPES, type CostType, type ImportKind } from "@/lib/domain";
import { money } from "@/lib/format";
import { fileSize, isTooLarge } from "@/lib/uploads";

type Step = "upload" | "mapping" | "review" | "done";
type KindInfo = Schemas["ImportKindInfo"];
type Report = Schemas["ImportReport"];

const STEPS: Step[] = ["upload", "mapping", "review", "done"];

/**
 * One file of a quarter at a time: which kind, the file, its columns, a preview that writes nothing,
 * then the import and what it unlocked. The kinds and their columns come from the API
 * (`/imports/kinds`), named with the same headers as the templates and the exports. The preview is
 * mandatory and is what gets committed: the costs file's default type and per-wording types are kept
 * on the job; if the figures moved in between, the API refuses (PREVIEW_CHANGED) and the new preview is
 * shown for another look — never imported behind the person's back.
 */
export default function ImportWizard({
  kinds,
  initialJob,
  initialKind,
  baseCurrency,
}: {
  kinds: KindInfo[];
  initialJob: ImportJobResponse | null;
  initialKind: ImportKind | null;
  baseCurrency: string;
}) {
  const t = useTranslations("importWizard");
  const costTypeLabel = useTranslations("domain.costType");
  const wt = useTranslations("workflow");
  const wLabel = (key: string, params?: Record<string, string | number>) => wt(key as never, params as never);
  const router = useRouter();
  const locale = useLocale();
  const options = (initialJob?.options ?? {}) as { default_cost_type?: CostType | null; label_types?: Record<string, CostType>; force_duplicates?: boolean };
  const [job, setJob] = useState<ImportJobResponse | null>(initialJob);
  const [step, setStep] = useState<Step>(initialJob ? "mapping" : "upload");
  const [kind, setKind] = useState<ImportKind>(initialJob?.kind ?? initialKind ?? "PURCHASE_ORDERS");
  const [file, setFile] = useState<File | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>(initialJob?.mapping ?? {});
  const [defaultCostType, setDefaultCostType] = useState<CostType | "">(options.default_cost_type ?? "");
  const [labelTypes, setLabelTypes] = useState<Record<string, CostType>>(options.label_types ?? {});
  // A person's word that lines refused as a possible double entry are other invoices: kept on the job,
  // so the commit writes them too, and logged with the import.
  const [forceDuplicates, setForceDuplicates] = useState(options.force_duplicates ?? false);
  // A refusal of the whole file at the preview (a tax-included amount column…) is about the mapping:
  // said where the mapping is, until it changes.
  const [mappingError, setMappingError] = useState<string | null>(null);
  const [changed, setChanged] = useState<{ before: Report; after: Report } | null>(null);
  const [existingId, setExistingId] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const tooLarge = isTooLarge(file);

  // Once a file is up, its own kind rules the columns and the preview, whatever the picker shows.
  const activeKind = step === "upload" ? kind : (job?.kind ?? kind);
  const info = kinds.find((k) => k.kind === activeKind);
  const fields = useMemo(() => info?.fields ?? [], [info]);
  // The same words as the template's and the exports' columns, in the reader's language: this kind's
  // header first, another kind's for a field it does not list.
  const fieldName = (field: string) => {
    const f = fields.find((x) => x.field === field) ?? kinds.flatMap((k) => k.fields).find((x) => x.field === field);
    return f ? (locale === "fr" ? f.header_fr : f.header_en) : field;
  };
  const issueSentence = useIssueSentence(activeKind, fieldName, baseCurrency);
  const report = job?.report;
  const errors = report?.errors ?? [];
  const warnings = report?.warnings ?? [];
  // Whether a value is per unit or for the whole line is exactly what the dropdown decides, so the
  // warning sits beside it (audit B19) once a preview has detected the ambiguity.
  const unitColumnWarning = (field: string) => warnings.find((w) => w.code === "UNIT_COLUMN_AMBIGUOUS" && w.field === field);
  const errorRows = new Set(errors.map((e) => e.row)).size;
  const missingRequired = fields.filter((f) => f.required && !mapping[f.field]).map((f) => fieldName(f.field));
  const missingOneOf = (info?.required_one_of ?? []).filter((group) => !group.some((f) => mapping[f])).map((group) => group.map(fieldName));

  const reset = (next?: ImportKind) => {
    setJob(null);
    setFile(null);
    setMapping({});
    setDefaultCostType("");
    setLabelTypes({});
    setForceDuplicates(false);
    setMappingError(null);
    setChanged(null);
    setExistingId(null);
    if (next) setKind(next);
    setStep("upload");
  };

  const upload = () => {
    if (!file || tooLarge) return;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("kind", kind);
    startTransition(async () => {
      // Refused before the action runs (connection dropped, a body over a limit): it rejects.
      const result = await createImport(fd).catch(() => null);
      if (!result) return void toast.error(t("upload.failed"));
      if (!result.ok) {
        if (result.existingId) return void setExistingId(result.existingId);
        return void toast.error(result.error);
      }
      setExistingId(null);
      setJob(result.data);
      setMapping(result.data.mapping);
      setStep("mapping");
    });
  };

  const validate = (next: { labelTypes?: Record<string, CostType>; forceDuplicates?: boolean } = {}) => {
    if (!job) return;
    const types = next.labelTypes ?? labelTypes;
    const force = next.forceDuplicates ?? forceDuplicates;
    startTransition(async () => {
      const result = await validateImport(job.id, mapping, { defaultCostType: defaultCostType || null, labelTypes: types, forceDuplicates: force });
      if (!result.ok) {
        const message = "oneOf" in result ? t("mapping.oneOf", { count: result.oneOf.length, fields: result.oneOf.map(fieldName).join(", ") }) : result.error;
        if (step !== "mapping") toast.error(message);
        setMappingError(message);
        return;
      }
      setMappingError(null);
      setLabelTypes(types);
      setForceDuplicates(force);
      setJob(result.data);
      setChanged(null);
      setStep("review");
    });
  };

  const commit = (onError: "skip_rows" | "abort") => {
    if (!job) return;
    startTransition(async () => {
      const result = await commitImport(job.id, onError, job.preview_key ?? null);
      if (!result.ok) {
        if (result.code === "PREVIEW_CHANGED" && result.report) {
          // The new preview, and the key that commits exactly it: the next click imports what is shown.
          setChanged({ before: job.report, after: result.report });
          setJob({ ...job, report: result.report, preview_key: result.previewKey ?? job.preview_key });
          return;
        }
        return void toast.error(result.error);
      }
      setJob(result.data);
      setStep("done");
      toast.success(t("done.committed"));
      router.refresh();
    });
  };

  // The figures the preview shows first, by kind: what the import would create or change.
  const figures = (r: Report): [string, string][] => {
    const valid: [string, string] = [t("review.validRows"), `${r.valid_rows} / ${r.row_count}`];
    switch (activeKind) {
      case "PRODUCTS":
        return [valid, [t("review.products"), t("review.newUpdated", { created: r.products_created ?? 0, updated: r.products_updated ?? 0 })]];
      case "CONTAINERS":
        return [
          valid,
          [t("review.containers"), t("review.newUpdatedMasculine", { created: r.containers_created ?? 0, updated: r.containers_updated ?? 0 })],
          [t("review.dated"), String(r.containers_dated ?? 0)],
          [t("review.shipmentsLoads"), `${r.shipments_created ?? 0} / ${r.loads_created ?? 0}`],
        ];
      case "COSTS":
        return [
          valid,
          [t("review.costs"), String(r.costs_created ?? 0)],
          [t("review.costsSkipped"), String(r.costs_skipped ?? 0)],
          [t("review.estimatesReplaced"), String(r.estimates_replaced ?? 0)],
        ];
      default:
        return [
          valid,
          [t("review.purchaseOrders"), t("review.newUpdated", { created: r.purchase_orders_created ?? 0, updated: r.purchase_orders_updated ?? 0 })],
          [t("review.lines"), t("review.newUpdated", { created: r.lines_created ?? 0, updated: r.lines_updated ?? 0 })],
          [t("review.containersLoads"), `${r.containers_created ?? 0} / ${(r.loads_created ?? 0) + (r.loads_updated ?? 0)}`],
        ];
    }
  };
  // Before → after, in money for a costs file, in rows otherwise.
  const changedFigure = (r: Report) => (r.money ? money(r.money.total_base, baseCurrency, locale) : t("review.rowsCount", { count: r.valid_rows }));

  const wizardStep = importWizardStep(step, job !== null && step === "upload", errors.length > 0);

  return (
    <div className="space-y-6">
      <NextStepCard
        title={wt("nextStep")}
        description={wLabel(wizardStep.descriptionKey, wizardStep.descriptionParams)}
        variant={wizardStep.variant}
        primary={{ href: wizardStep.primary.href, label: wLabel(wizardStep.primary.labelKey, wizardStep.primary.labelParams) }}
        secondary={wizardStep.secondary.map((s) => ({ href: s.href, label: wLabel(s.labelKey, s.labelParams) }))}
      />
      <ol className="flex flex-wrap gap-2 text-xs">
        {STEPS.map((s) => (
          <li key={s} aria-current={s === step ? "step" : undefined} className={`px-3 py-1.5 rounded-md border ${s === step ? "bg-slate-900 text-white border-slate-900" : "bg-white text-slate-500 border-slate-200"}`}>{t(`steps.${s}`)}</li>
        ))}
      </ol>

      {step === "upload" ? (
        <Card title={t("upload.card")}>
          <div className="p-5 space-y-5">
            <KindPicker kinds={kinds} value={kind} onChange={(k) => { setKind(k); setExistingId(null); }} />
            <div className="max-w-xl space-y-4">
              <label className="relative border-2 border-dashed border-slate-300 rounded-md p-8 flex flex-col items-center justify-center text-center hover:bg-slate-50 cursor-pointer">
                <input
                  type="file"
                  accept=".csv,.xlsx,text/csv"
                  onChange={(e) => { setFile(e.target.files?.[0] ?? null); setExistingId(null); }}
                  className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                  aria-label={t("upload.chooseFor", { kind: t(`kinds.${kind}.title`) })}
                  aria-invalid={tooLarge || undefined}
                  aria-describedby={tooLarge ? "import-file-too-large" : undefined}
                />
                {file ? (
                  <>{tooLarge ? <FileWarning className="w-8 h-8 text-red-600 mb-2" aria-hidden /> : <CheckCircle2 className="w-8 h-8 text-emerald-500 mb-2" aria-hidden />}<p className="text-sm font-medium">{file.name}</p><p className="text-xs text-slate-500 mt-1">{fileSize(file.size, locale)}</p></>
                ) : (
                  <><FileType className="w-8 h-8 text-slate-400 mb-2" aria-hidden /><p className="text-sm font-medium text-slate-700">{t("upload.cta")}</p><p className="text-xs text-slate-500 mt-1">{t("upload.hint")}</p></>
                )}
              </label>
              {tooLarge ? <FileTooLarge id="import-file-too-large" file={file} kind="import" /> : null}
              {existingId ? (
                <p role="alert" className="text-sm text-amber-900">
                  {t("upload.alreadyImported")}{" "}
                  <Link href={`/imports#import-${existingId}`} className="text-blue-700 underline underline-offset-2 hover:no-underline">{t("upload.openExisting")}</Link>
                </p>
              ) : null}
              <button type="button" onClick={upload} disabled={!file || tooLarge || pending} className={btnPrimary}>
                {pending ? <Loader2 className="w-4 h-4 mr-2 animate-spin" aria-hidden /> : null}{t("upload.submit")}
              </button>
            </div>
          </div>
        </Card>
      ) : null}

      {step === "mapping" && job ? (
        <Card title={t("mapping.card", { filename: job.original_filename ?? t("mapping.fallbackFilename") })} right={<span className="text-xs text-slate-500">{t("mapping.meta", { rows: job.row_count, encoding: job.encoding ?? "" })}{job.delimiter ? ` · "${job.delimiter}"` : ""}</span>}>
          <div className="p-5 space-y-4">
            <p className="text-sm text-slate-600">{t("mapping.kind", { kind: t(`kinds.${job.kind}.title`) })}</p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-3 max-w-3xl">
              {fields.map((f) => {
                const warning = unitColumnWarning(f.field);
                return (
                  <div key={f.field} className="flex flex-col gap-1">
                    <div className="flex items-center gap-3">
                      <label htmlFor={`map-${f.field}`} className="w-44 text-sm text-slate-700 shrink-0">{fieldName(f.field)}{f.required ? <span className="text-red-600" aria-hidden> *</span> : null}{f.required ? <span className="sr-only"> {t("mapping.requiredMark")}</span> : null}</label>
                      <select id={`map-${f.field}`} value={mapping[f.field] ?? ""} onChange={(e) => { setMappingError(null); setMapping((m) => { const n = { ...m }; if (e.target.value) n[f.field] = e.target.value; else delete n[f.field]; return n; }); }} className={input}>
                        <option value="">{t("mapping.notInFile")}</option>
                        {job.columns.map((c) => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </div>
                    {warning ? <p className="text-xs text-amber-700 pl-[calc(11rem+0.75rem)]">{issueSentence(warning)}</p> : null}
                  </div>
                );
              })}
            </div>
            {job.kind === "COSTS" ? (
              <div className="max-w-xl">
                <label htmlFor="default-cost-type" className={label}>{t("mapping.defaultType")}</label>
                <select id="default-cost-type" value={defaultCostType} onChange={(e) => setDefaultCostType(e.target.value as CostType | "")} aria-describedby="default-cost-type-hint" className={input}>
                  <option value="">{t("mapping.defaultTypeNone")}</option>
                  {COST_TYPES.map((c) => <option key={c} value={c}>{costTypeLabel(c)}</option>)}
                </select>
                <p id="default-cost-type-hint" className="mt-1 text-xs text-slate-500">{t("mapping.defaultTypeHint")}</p>
              </div>
            ) : null}
            {missingRequired.length ? <p className="text-sm text-amber-700">{t("mapping.required", { fields: missingRequired.join(", ") })}</p> : null}
            {missingOneOf.map((group) => (
              <p key={group.join()} className="text-sm text-amber-700">{t("mapping.oneOf", { count: group.length, fields: group.join(", ") })}</p>
            ))}
            {mappingError ? <p role="alert" className="text-sm text-red-700">{mappingError}</p> : null}
            <div className="flex gap-2">
              <button type="button" onClick={() => setStep("upload")} className={btnSecondary}>{t("mapping.back")}</button>
              <button type="button" onClick={() => validate()} disabled={pending || missingRequired.length > 0 || missingOneOf.length > 0} className={btnPrimary}>
                {pending ? <Loader2 className="w-4 h-4 mr-2 animate-spin" aria-hidden /> : null}{t("mapping.validate")}
              </button>
            </div>
          </div>
        </Card>
      ) : null}

      {step === "review" && job && report ? (
        <Card title={t("review.card")} right={<span className="text-xs text-slate-500">{t("review.nothingWritten")}</span>}>
          <div className="p-5 space-y-5">
            {changed ? (
              <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950">
                {t("review.changed", { before: changedFigure(changed.before), after: changedFigure(changed.after) })}
              </div>
            ) : null}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
              {figures(report).map(([k, v]) => (
                <div key={k} className="bg-slate-50 rounded-md px-3 py-2"><div className="text-xs text-slate-500">{k}</div><div className="font-medium tabular-nums">{v}</div></div>
              ))}
            </div>
            {/* What the reader decided about the file itself, before any row (B22/B19): a header on
                line 3 or a skipped totals row used to look like lost rows. */}
            {report.header_row > 1 ? <p className="text-xs text-slate-500">{t("review.headerRow", { line: report.header_row })}</p> : null}
            {report.totals_row_ignored ? <p className="text-xs text-slate-500">{t("review.totalsRowIgnored")}</p> : null}
            {report.forward_filled_rows ? <p className="text-xs text-slate-500">{t("review.forwardFilled", { count: report.forward_filled_rows })}</p> : null}
            {job.kind === "COSTS" ? <CostsPreview report={report} baseCurrency={baseCurrency} labelTypes={labelTypes} pending={pending} onRetype={(types) => validate({ labelTypes: types })} /> : null}
            {job.kind === "COSTS" && forceDuplicates ? (
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950">
                <span>{t("issues.forced")}</span>
                <button type="button" onClick={() => validate({ forceDuplicates: false })} disabled={pending} className="text-sm font-medium text-blue-700 underline underline-offset-2 hover:no-underline">{t("issues.unforce")}</button>
              </div>
            ) : null}
            {errors.length ? (
              <IssueGroups
                title={t("review.errorSummary", { rows: errorRows, errors: errors.length })}
                issues={errors}
                tone="red"
                kind={job.kind}
                fieldName={fieldName}
                baseCurrency={baseCurrency}
                actions={{
                  DUPLICATE_COST: (
                    <button type="button" onClick={() => validate({ forceDuplicates: true })} disabled={pending} className={btnSecondary}>{t("issues.forceDuplicates")}</button>
                  ),
                }}
              />
            ) : (
              <p className="text-sm text-emerald-700">{t("review.noError")}</p>
            )}
            {warnings.length ? <IssueGroups title={t("review.warningCount", { count: warnings.length })} issues={warnings} tone="amber" kind={job.kind} fieldName={fieldName} baseCurrency={baseCurrency} /> : null}
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={() => setStep("mapping")} className={btnSecondary}>{t("review.backToMapping")}</button>
              <button type="button" onClick={() => commit("skip_rows")} disabled={pending || report.valid_rows === 0} className={btnPrimary}>
                {pending ? <Loader2 className="w-4 h-4 mr-2 animate-spin" aria-hidden /> : null}{errors.length ? t("review.importValid") : t("review.import")}
              </button>
              {errors.length ? <span className="text-xs text-slate-500 self-center">{t("review.orFix")}</span> : null}
            </div>
          </div>
        </Card>
      ) : null}

      {step === "done" && job ? (
        <Card title={t("done.card")}>
          <ImportDone job={job} baseCurrency={baseCurrency} onAnother={(next) => reset(next)} />
        </Card>
      ) : null}
    </div>
  );
}
