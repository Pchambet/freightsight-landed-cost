/** Translate machine-readable invoice line notes (`@code|arg|…`) from the API. */

import { dateShort, money } from "@/lib/format";

type NoteTranslator = (key: string, values?: Record<string, string | number>) => string;
type CostTypeTranslator = (key: string) => string;

/**
 * `invoiceCurrency` is the invoice's own currency: `arithmetic_mismatch` carries only a bare number
 * (the check is a same-currency sum, so the API never repeats the currency in it), but a bare number
 * with no symbol reads badly in a sentence ("300.00 EUR" in a French sentence) — every amount
 * on screen goes through `money()`, this one included.
 */
export function formatInvoiceNote(
  note: string,
  t: NoteTranslator,
  costType: CostTypeTranslator,
  locale: string,
  invoiceCurrency: string,
): string {
  if (!note.startsWith("@")) return note;
  const [code, ...parts] = note.slice(1).split("|");
  switch (code) {
    case "single_container":
      return t("singleContainer", { container: parts[0] ?? "" });
    case "duplicate_cost":
      // A 6th parameter (the existing cost's own scope) rides along since the erp/invoices lanes'
      // cross-scope fix — absent on a note written before it, in which case the sentence falls back
      // to a neutral wording via ICU's `other` arm rather than showing an empty scope.
      return t("duplicateCost", {
        type: costType(parts[0] ?? ""),
        vendor: parts[1] || "—",
        invoice: parts[2] || "—",
        amount: money(parts[3] ?? "0", parts[4] || invoiceCurrency, locale),
        scope: parts[5] ?? "",
      });
    case "default_container":
      return t("defaultContainer", { container: parts[0] ?? "" });
    case "unknown_container":
      return t("unknownContainer", { container: parts[0] ?? "" });
    case "no_fx_rate":
      return t("noFxRate", { currency: parts[0] ?? "", date: parts[1] ? dateShort(parts[1], locale) : "—" });
    case "arithmetic_mismatch":
      return t(parts[1] === "more_than" ? "arithmeticMore" : "arithmeticLess", { amount: money(parts[0] ?? "0", invoiceCurrency, locale) });
    case "unverified_total":
      return t("unverifiedTotal");
    case "multi_container":
      return t("multiContainer");
    case "credit_note":
      return t("creditNote");
    case "negative_line":
      return t("negativeLine", { amount: money(parts[0] ?? "0", invoiceCurrency, locale) });
    case "dropped_line":
      return t("droppedLine", { description: parts[0] ?? "", amount: parts[1] ? money(parts[1], invoiceCurrency, locale) : "—" });
    case "currency_guess":
      return t("currencyGuess", { chosen: parts[0] ?? "", others: parts.slice(1).join(", ") });
    case "added_by_human":
      return t("addedByHuman");
    case "forced_duplicate":
      return t("forcedDuplicate", {
        line_no: parts[0] ?? "",
        type: costType(parts[1] ?? ""),
        vendor: parts[2] || "—",
        invoice: parts[3] || "—",
        amount: money(parts[4] ?? "0", parts[5] || invoiceCurrency, locale),
      });
    case "pushed_cost":
      return t("pushedCost", { document: parts[0] ?? "", state: parts[1] ?? "" });
    case "forced_recorded":
      // `@forced_recorded|number|vendor`: confirmed past INVOICE_ALREADY_RECORDED — the person said
      // it is another invoice than the one already in the books under that number.
      return t("forcedRecorded", { invoice: parts[0] || "—", vendor: parts[1] || "—" });
    case "several_estimates":
      // `@several_estimates|lines|count|type`: more than one open estimate of that type on the line's
      // target, so none was replaced — which one this invoice settles is a person's call.
      return t("severalEstimates", {
        lines: parts[0] ?? "",
        lineCount: (parts[0] ?? "").split(",").filter((x) => x.trim()).length,
        count: Number(parts[1] ?? 0),
        type: costType(parts[2] ?? ""),
      });
    default:
      return note;
  }
}

/** A note that records a fact (where a line was attached, who added it) rather than asking for a check. */
export function isInformativeNote(note: string): boolean {
  return /^@(single_container|default_container|added_by_human|pushed_cost)(\||$)/.test(note);
}

export function isDuplicateCostNote(note: string): boolean {
  return note.startsWith("@duplicate_cost|");
}

export function splitInvoiceNotes(notes: string | null | undefined): string[] {
  if (!notes) return [];
  return notes.split("; ").map((n) => n.trim()).filter(Boolean);
}
