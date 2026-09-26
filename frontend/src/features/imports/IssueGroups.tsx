"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { td, th } from "@/components/layout/AppShell";
import ScrollRegion from "@/components/shared/ScrollRegion";
import type { Schemas } from "@/lib/api/client";
import type { ImportKind } from "@/lib/domain";
import { dateShort, decimalDisplay, money, rate as formatRate } from "@/lib/format";

type Issue = Schemas["ImportRowIssue"];

/**
 * What fixes a whole cause, rather than a row: the file to import first, or what to change in this
 * one. Only codes with a remedy that holds for every row of the group are here; the others say their
 * own sentence and nothing more. A link may depend on the rows: one waiting invoice is opened itself.
 */
const REMEDY: Record<string, { key: string; href?: string | ((rows: Issue[]) => { href: string; linkKey: string }) }> = {
  UNKNOWN_CONTAINER: { key: "importContainers", href: "/imports/new?kind=CONTAINERS" },
  UNKNOWN_SHIPMENT: { key: "importContainers", href: "/imports/new?kind=CONTAINERS" },
  UNKNOWN_ORDER: { key: "importOrders", href: "/imports/new?kind=PURCHASE_ORDERS" },
  NO_TARGET: { key: "noTarget", href: "/imports/new?kind=CONTAINERS" },
  COST_TYPE_UNKNOWN: { key: "typeLabels" },
  COST_TYPE_AMBIGUOUS: { key: "splitLine" },
  ARRIVAL_UNKNOWN: { key: "arrival" },
  PO_UNKNOWN: { key: "ordersThenAgain", href: "/imports/new?kind=PURCHASE_ORDERS" },
  INVOICE_ALREADY_RECORDED: { key: "alreadyInBooks" },
  INVOICE_LINE_ALREADY_RECORDED: { key: "alreadyInBooks" },
  TARGET_AMBIGUOUS: { key: "oneContainer" },
  DUPLICATE_COST: { key: "maybeSecondInvoice" },
  INVOICE_IN_INBOX: {
    key: "inInbox",
    href: (rows) => {
      const ids = new Set(rows.map((i) => (i.params as Record<string, string> | null)?.invoice_id).filter(Boolean));
      return ids.size === 1 ? { href: `/invoices/${[...ids][0]}`, linkKey: "inInboxOne" } : { href: "/invoices?filter=review", linkKey: "inInbox" };
    },
  },
};

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

/** A decimal string without its sign — text, never a float. */
export const unsigned = (amount: string) => amount.replace(/^-/, "");

/** As many different sentences as a group says in full: a file's date columns, one warning each. */
const FEW = 6;

/**
 * The sentence of one row's issue. A code can mean something else in another file — a date to come is
 * a typo in a costs ledger and an ETA in a tracking sheet — so the kind's own sentence comes first
 * (`importWizard.row.<KIND>.<CODE>`), then the one every file shares (`row.ALL`), then the API's
 * (`errors`). Params are shown as the rest of the screen shows them, not raw: a date as a date, an
 * amount as money, a cost type by its name, a field by the header of the mapping step (`field`, the
 * column the row is refused on; the two columns of DATES_OUT_OF_ORDER are field names too). Some codes
 * come in two forms, which a `select` in the sentence tells apart.
 */
export function useIssueSentence(kind: ImportKind, fieldName: (field: string) => string, baseCurrency: string) {
  const row = useTranslations("importWizard.row");
  const errorCode = useTranslations("errors");
  const review = useTranslations("importWizard.review");
  const costType = useTranslations("domain.costType");
  const locale = useLocale();
  const asDay = (v: string) => (ISO_DAY.test(v) ? dateShort(v, locale) : v);
  const typeName = (v: string) => (costType.has(v) ? costType(v) : v);

  const params = (i: Issue): Record<string, string | number> => {
    const p = { ...((i.params ?? {}) as Record<string, string>) };
    const out: Record<string, string | number> = { ...p, field: i.field ? fieldName(i.field) : "" };
    // What is already in the books, in one phrase: its type (unless the sentence names it), forwarder,
    // invoice and amount.
    const recorded = (withType: boolean) =>
      [withType ? typeName(p.cost_type ?? "") : "", p.vendor, p.invoice_number ? row("invoiceNo", { number: p.invoice_number }) : "", p.amount_base ? money(p.amount_base, baseCurrency, locale) : ""]
        .filter(Boolean)
        .join(" · ");
    switch (i.code) {
      case "FX_RATE_DEFAULTED":
      case "FX_RATE_APPLIED":
        return { ...out, rate_date: dateShort(p.rate_date, locale), rate: formatRate(p.rate, locale) };
      case "FX_RATE_UNAVAILABLE":
        return { ...out, date: asDay(p.date ?? "") };
      case "DATES_OUT_OF_ORDER":
        return { ...out, first: fieldName(p.first), second: fieldName(p.second) };
      case "CONFLICT_IN_FILE":
        return p.shipment_reference
          ? { ...out, on: "bill", values: (p.values ?? "").split(", ").map(asDay).join(", ") }
          : { ...out, on: "container", first: asDay(p.first ?? ""), second: asDay(p.second ?? "") };
      case "COST_TYPE_UNKNOWN":
        return { ...out, from: i.field === "cost_type" ? "column" : "label" };
      case "COST_TYPE_AMBIGUOUS":
        return { ...out, named: p.types ? "types" : "mixed", types: (p.types ?? "").split(",").filter(Boolean).map(typeName).join(", ") };
      case "NO_TARGET":
        return { ...out, hasLabel: p.value ? "yes" : "no" };
      case "NEGATIVE":
        return { ...out, value: decimalDisplay(p.value, locale) };
      case "REVERSED_IN_FILE":
        return { ...out, amount: money(p.amount, p.currency, locale) };
      case "CREDIT_NOTE_SKIPPED":
        // « Un avoir de 100 € »: the file writes it negative, the sentence says what it is — and
        // whether it cancels a cost still counted.
        return { ...out, amount: money(unsigned(p.amount), p.currency, locale), reverses: p.reverses_cost_id ? "yes" : "no" };
      case "VAT_NOT_A_COST":
        return { ...out, on: i.field === "account" ? "account" : "label" };
      case "INVOICE_ALREADY_RECORDED":
      case "INVOICE_LINE_ALREADY_RECORDED":
        return { ...out, recorded: recorded(true) };
      case "DUPLICATE_COST":
        return { ...out, recorded: recorded(false), type: typeName(p.cost_type ?? "") };
      case "SEVERAL_ESTIMATES":
      case "ESTIMATE_OTHER_SCOPE":
        return { ...out, estimates: Number(p.estimates ?? 0), type: typeName(p.cost_type ?? "") };
      default:
        return out;
    }
  };

  return (i: Issue): string => {
    const own = [`${kind}.${i.code}`, `ALL.${i.code}`].find((k) => row.has(k));
    if (own) return row(own, params(i));
    return errorCode.has(i.code) ? errorCode(i.code, params(i)) : review("unknownIssue");
  };
}

/**
 * The preview's errors or warnings, one block per cause: its sentence (or, when rows differ, each
 * sentence, or one as an example), which rows, and what fixes them all — « 14 lignes visent des
 * conteneurs inconnus : importez d'abord le suivi des conteneurs ». Every row stays one click away.
 * Field names and codes are internal: the field is named with the mapping step's headers, the code
 * only through its translated sentence. `actions` puts a decision under a cause — the only one today
 * lets a person import lines refused as a possible double entry.
 */
export default function IssueGroups({
  issues,
  tone,
  title,
  kind,
  fieldName,
  baseCurrency,
  actions = {},
}: {
  issues: Issue[];
  tone: "amber" | "red";
  title: string;
  kind: ImportKind;
  fieldName: (field: string) => string;
  baseCurrency: string;
  actions?: Partial<Record<string, ReactNode>>;
}) {
  const t = useTranslations("importWizard.issues");
  const th_ = useTranslations("importWizard.review.th");
  const sentence = useIssueSentence(kind, fieldName, baseCurrency);
  const byCode = new Map<string, Issue[]>();
  for (const i of issues) byCode.set(i.code, [...(byCode.get(i.code) ?? []), i]);
  const groups = [...byCode].sort((a, b) => b[1].length - a[1].length);
  const color = tone === "amber" ? "text-amber-800" : "text-red-700";
  const border = tone === "amber" ? "border-amber-200 bg-amber-50/40" : "border-red-200 bg-red-50/40";
  return (
    <div>
      <div className={`text-sm font-medium mb-2 ${color}`}>{title}</div>
      <ul className="space-y-2">
        {groups.map(([code, rows]) => {
          const remedy = REMEDY[code];
          const link = typeof remedy?.href === "function" ? remedy.href(rows) : remedy?.href ? { href: remedy.href, linkKey: remedy.key } : null;
          // Rows, not issues: a file-wide warning sits on the header row once per column.
          const lines = [...new Set(rows.map((i) => i.row))];
          const shown = lines.slice(0, 8);
          // One sentence for the group when every row says the same; otherwise the few there are, or
          // the first one as an example — never one row's detail presented as every row's.
          const sentences = [...new Set(rows.map(sentence))];
          return (
            <li key={code} className={`rounded-md border px-3 py-2 text-sm ${border}`}>
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                {sentences.length === 1 ? (
                  <span className="text-slate-800">{sentences[0]}</span>
                ) : sentences.length <= FEW ? (
                  <ul className="text-slate-800 space-y-0.5">{sentences.map((s) => <li key={s}>{s}</li>)}</ul>
                ) : (
                  <span className="text-slate-800">{t("example", { row: rows[0].row, sentence: sentences[0] })}</span>
                )}
                <span className="text-xs text-slate-600 tabular-nums">
                  {t("rows", { count: lines.length, list: shown.join(", "), more: lines.length - shown.length })}
                </span>
              </div>
              {remedy ? (
                <p className="mt-1 text-xs text-slate-700">
                  {t(`remedy.${remedy.key}`)}
                  {link ? (
                    <>
                      {" "}
                      <Link href={link.href} className="text-blue-700 underline underline-offset-2 hover:no-underline">{t(`remedyLink.${link.linkKey}`)}</Link>
                    </>
                  ) : null}
                </p>
              ) : null}
              {actions[code] ? <div className="mt-2">{actions[code]}</div> : null}
              {lines.length > 1 || sentences.length > FEW ? (
                <details className="mt-1">
                  <summary className="cursor-pointer text-xs text-slate-600">{t("seeAll", { count: rows.length })}</summary>
                  <ScrollRegion label={sentences.length === 1 ? sentences[0] : t("detailOf", { list: shown.join(", ") })} className="mt-2 border border-slate-200 rounded-md max-h-64 overflow-y-auto bg-white">
                    <table className="min-w-full divide-y divide-slate-200 text-xs">
                      <thead className="bg-slate-50"><tr><th className={th}>{th_("row")}</th><th className={th}>{th_("field")}</th><th className={th}>{th_("message")}</th></tr></thead>
                      <tbody className="divide-y divide-slate-100">
                        {rows.slice(0, 200).map((i, idx) => (
                          <tr key={idx}>
                            <td className={td}>{i.row}</td>
                            <td className={td}>{i.field ? fieldName(i.field) : "—"}</td>
                            <td className="px-4 py-2">{sentence(i)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </ScrollRegion>
                </details>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
