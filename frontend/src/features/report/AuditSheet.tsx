import Link from "next/link";
import { getTranslations } from "next-intl/server";
import ScrollRegion from "@/components/shared/ScrollRegion";
import { monthTitle } from "@/features/overview/period";
import type { Schemas } from "@/lib/api/client";
import { dateShort, money, qty } from "@/lib/format";

type Audit = Schemas["AuditReport"];
type Finding = Schemas["AuditFinding"];

export type AuditHeader = {
  issuer: string;
  generatedAt: string;
  expiresAt?: string | null;
  sampleData?: boolean;
  /** The sender left vendors and invoice numbers out: the snapshot does not hold them. */
  redacted?: boolean;
  /** In the application a container is a link; in a document that leaves it, a number. */
  linkContainers?: boolean;
};

/**
 * The audit of a period, as a document a CFO puts in front of the owner — and the deliverable of the
 * paid audit offer. It is ordered by what makes a decision, not by what is easy to compute: the money
 * first (what pricing on the purchase price hides, what to have the providers justify, what the
 * delays cost), then the proof of it, then how the figures were made and how complete they are.
 *
 * Every amount is a field of the API, never a sum made here: the headline figures are, on the API
 * side, tested to be the sums of their sections. And one rule governs the wording: a finding is never
 * an accusation nor a promised saving. "Établi" is a measured fact; "à vérifier" is a question.
 */
export default async function AuditSheet({ audit, header, locale }: { audit: Audit; header: AuditHeader; locale: "fr" | "en" }) {
  const [t, tf, tc, tt, tfx, terr] = await Promise.all([
    getTranslations({ locale, namespace: "costAudit.sheet" }),
    getTranslations({ locale, namespace: "domain.finding" }),
    getTranslations({ locale, namespace: "domain.confidence" }),
    getTranslations({ locale, namespace: "domain.costType" }),
    getTranslations({ locale, namespace: "domain.fxSource" }),
    getTranslations({ locale, namespace: "errors" }),
  ]);
  const cur = audit.base_currency;
  const eur = (v: string | number | null | undefined) => money(v, cur, locale);
  const ratio = (v: string | null | undefined) =>
    v === null || v === undefined ? "—" : new Intl.NumberFormat(locale === "fr" ? "fr-FR" : "en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(v));
  const points = (v: string) => new Intl.NumberFormat(locale === "fr" ? "fr-FR" : "en-GB", { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(Number(v));
  // Percentages come with one decimal from the API: shown with one, never with a precision it did not compute.
  const pct1 = (v: string) => `${points(v)} %`;
  /** "+8 600,00 €" / "−2 300,00 €": a gap always wears its sign. Formatting only. */
  const signed = (v: string | null | undefined, currency = cur) => {
    const n = Number(v ?? 0);
    return `${n > 0 ? "+" : n < 0 ? "−" : ""}${money(Math.abs(n), currency, locale)}`;
  };
  /** A threshold as a person writes it: "2 %", "1,5", not "2,0 %". */
  const plain = (v: string | number) => new Intl.NumberFormat(locale === "fr" ? "fr-FR" : "en-GB", { maximumFractionDigits: 2 }).format(Number(v));
  /** Cost type labels inside a sentence: lower-cased, except an acronym ("TVA", "THC") that starts one. */
  const types = (list: string[]) =>
    list
      .map((k) => (tt.has(k) ? tt(k) : k))
      .map((l) => (l.length > 1 && l[1] === l[1].toLowerCase() ? l[0].toLowerCase() + l.slice(1) : l))
      .join(", ");
  const rules = audit.rules;
  const h = audit.headline;
  // Which findings a claim can be made about is the API's call (`recoverable`), not a list kept here.
  const claims = audit.findings.filter((f) => f.recoverable);
  const quality = audit.findings.filter((f) => !f.recoverable);
  const priced = audit.margins.some((m) => m.sale_price !== null && m.sale_price !== undefined);
  const assumed = audit.assumed_coefficient ?? null;
  const above = (coefficient: string | null | undefined) => assumed !== null && coefficient !== null && coefficient !== undefined && Number(coefficient) > Number(assumed);
  const c = audit.completeness;
  const label = "text-[10px] font-medium uppercase tracking-wider text-slate-500";
  const thc = "px-3 py-2 text-left text-[10px] font-medium uppercase tracking-wider text-slate-500";
  const tdc = "px-3 py-2 align-top";
  const section = "px-8 pb-7";
  const h2 = "text-base font-semibold text-slate-900";

  const box = (f: Finding) => {
    if (!f.container_number) return null;
    return header.linkContainers && f.container_id ? (
      <Link href={`/container/${f.container_id}`} className="font-mono text-blue-700 hover:underline">{f.container_number}</Link>
    ) : (
      <span className="font-mono">{f.container_number}</span>
    );
  };

  /** The rule's own figures, in a sentence: what was compared with what. */
  const because = (f: Finding): string | null => {
    const p = (f.params ?? {}) as Record<string, string>;
    const lines = [p.skus, p.po_numbers].filter(Boolean).join(" · ");
    switch (f.code) {
      case "DUPLICATE_CHARGE":
        return p.first_invoice || p.first_vendor
          ? t(p.same_vendor === "true" ? "because.duplicateSameVendor" : "because.duplicate", { vendor: p.first_vendor || "—", invoice: p.first_invoice || "—", amount: eur(p.first_amount) })
          : t("because.duplicateRedacted", { amount: eur(p.first_amount) });
      case "DOUBLE_ENTRY": {
        // Not a forwarder billing twice: the company's own books hold one invoice twice, under two
        // spellings. Only an established pair (the same line, named on both sides) is told to delete
        // an entry; « à vérifier » — a box and its B/L, two currencies, a missing forwarder — may be two
        // true lines of one invoice, and the sentence says to check before deleting anything.
        const sure = f.confidence === "sure";
        return p.first_invoice || p.first_vendor
          ? t(sure ? "because.doubleEntry" : "because.doubleEntryToCheck", { vendor: p.first_vendor || "—", invoice: p.first_invoice || "—", amount: eur(p.first_amount) })
          : t(sure ? "because.doubleEntryRedacted" : "because.doubleEntryToCheckRedacted", { amount: eur(p.first_amount) });
      }
      case "ABOVE_QUOTE": {
        // An estimate and an invoice in two currencies are compared in euros at each document's own
        // rate: the gap mixes price and exchange, which is why the rule only says "to check".
        if (f.confidence === "to_check") return t("because.aboveQuoteMixed", { quoted: eur(p.quoted), invoiced: eur(p.invoiced), share: points(p.share_pct ?? "0") });
        const currency = p.currency || cur;
        const base = t("because.aboveQuote", { quoted: money(p.quoted, currency, locale), invoiced: money(p.invoiced, currency, locale), share: points(p.share_pct ?? "0"), amount: eur(f.amount) });
        const fx = Number(p.fx_effect ?? 0);
        // The exchange rate's part of the gap in euros is the rate's, not the provider's: said apart.
        return fx > 0 ? `${base} ${t("because.fxAdded", { fx: eur(fx) })}` : fx < 0 ? `${base} ${t("because.fxRemoved", { fx: eur(-fx) })}` : base;
      }
      case "OUTLIER_CHARGE":
        return t(p.size ? "because.outlierSized" : "because.outlier", { amount: eur(p.amount), median: eur(p.median), ratio: points(p.ratio ?? "0"), basis: f.basis ?? 0, route: p.route ?? "—", size: p.size ?? "" });
      case "ESTIMATE_NEVER_INVOICED":
        return t("because.stale", { days: Number(p.days ?? 0), date: dateShort(p.landed_on, locale), asOf: dateShort(audit.as_of, locale) });
      case "UNALLOCATED_COST": {
        const reason = p.reason && terr.has(p.reason) ? terr(p.reason) : t("because.unallocated");
        return lines ? `${reason} (${lines})` : reason;
      }
      case "DUTY_RATE_MISSING":
        return t("because.dutyRate", { lines: Number(p.lines ?? 0), which: lines || "—" });
      default:
        return null;
    }
  };

  const findingRow = (f: Finding, i: number) => (
    <li key={`${f.code}-${f.cost_ids.join("-")}-${i}`} className="py-3 print:break-inside-avoid">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider ring-1 ring-inset ${f.confidence === "sure" ? "bg-slate-900 text-white ring-slate-900" : "bg-amber-50 text-amber-900 ring-amber-600/30"}`}>
          {tc.has(f.confidence) ? tc(f.confidence) : f.confidence}
        </span>
        <span className="font-medium text-slate-900">{tf.has(f.code) ? tf(f.code) : f.code}</span>
        <span className="ml-auto font-semibold tabular-nums text-slate-900">{eur(f.amount)}</span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-2 text-xs text-slate-600">
        {box(f)}
        {f.cost_type ? <span>{tt.has(f.cost_type) ? tt(f.cost_type) : f.cost_type}</span> : null}
        {!header.redacted && (f.vendor || f.invoice_number) ? <span className="text-slate-500">{[f.vendor, f.invoice_number].filter(Boolean).join(" · ")}</span> : null}
      </div>
      {because(f) ? <p className="mt-1 text-sm text-slate-700">{because(f)}</p> : null}
    </li>
  );

  return (
    <article className="mx-auto max-w-[56rem] bg-white text-slate-900 rounded-xl border border-slate-200 shadow-card print:shadow-none print:border-0 print:rounded-none print:max-w-none [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
      <header className="px-8 pt-8 pb-6 border-b border-slate-200 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className={label}>{t("kicker")}</div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">{t("title")}</h1>
          <div className="mt-1 text-sm text-slate-600">{t("period", { from: dateShort(audit.period_from, locale), to: dateShort(audit.period_to, locale) })}</div>
        </div>
        <div className="text-right text-sm">
          <div className="font-semibold">{header.issuer}</div>
          <div className="text-slate-600">{t("computedOn", { date: dateShort(audit.as_of ?? header.generatedAt, locale) })}</div>
          {header.expiresAt ? <div className="text-xs text-slate-500">{t("validUntil", { date: dateShort(header.expiresAt, locale) })}</div> : null}
        </div>
      </header>

      {header.sampleData ? <p className="mx-8 mt-5 rounded-md border border-violet-200 bg-violet-50 px-3 py-2 text-xs text-violet-950">{t("sample")}</p> : null}

      {audit.completeness.containers === 0 ? (
        <section className="px-8 py-10 text-center">
          <h2 className="text-base font-semibold text-slate-900">{t("empty.title")}</h2>
          <p className="mt-1 text-sm text-slate-600">{t("empty.body")}</p>
        </section>
      ) : (
        <>
          {/* 1 · The money, on the first page: the rest of the document is its proof. */}
          <section className="px-8 py-6 print:break-inside-avoid" aria-labelledby="audit-essentials">
            <h2 id="audit-essentials" className={h2}>{t("essentials.title")}</h2>
            <div className="mt-4 rounded-lg border border-slate-200 p-4 grid gap-4 sm:grid-cols-[auto_1fr] sm:items-center">
              <div>
                <div className={label}>{t("essentials.coefficient")}</div>
                <div className="mt-1 text-3xl font-semibold figure-proportional">{ratio(audit.coefficient)}</div>
              </div>
              {assumed === null || audit.coefficient === null || h.gap_vs_assumed === null ? (
                <p className="text-sm text-slate-600">{t("essentials.coefficientNoAssumed")}</p>
              ) : (
                <div className="sm:border-l sm:border-slate-200 sm:pl-4">
                  <div className={label}>{Number(h.gap_vs_assumed ?? 0) > 0 ? t("essentials.gapUncovered") : t("essentials.gapCovered")}</div>
                  <div className={`mt-1 text-2xl font-semibold figure-proportional ${Number(h.gap_vs_assumed ?? 0) > 0 ? "text-red-700" : "text-emerald-800"}`}>{signed(h.gap_vs_assumed)}</div>
                  <p className="mt-1 text-xs text-slate-600">
                    {Number(h.gap_vs_assumed ?? 0) > 0
                      ? t("essentials.gapUncoveredSub", { assumed: ratio(assumed), real: ratio(audit.coefficient) })
                      : t("essentials.gapCoveredSub", { assumed: ratio(assumed), real: ratio(audit.coefficient) })}
                  </p>
                </div>
              )}
            </div>
            <div className="mt-4 grid gap-4 sm:grid-cols-3">
              <div className="rounded-lg border border-slate-200 p-4">
                <div className={label}>{t("essentials.claims")}</div>
                <div className="mt-1 text-2xl font-semibold figure-proportional">{eur(h.recoverable_sure)}</div>
                <p className="mt-1 text-xs text-slate-600">{t("essentials.claimsSub", { toCheck: eur(h.recoverable_to_check) })}</p>
              </div>
              <div className="rounded-lg border border-slate-200 p-4">
                <div className={label}>{t("essentials.margin")}</div>
                <div className="mt-1 text-2xl font-semibold figure-proportional">{h.priced_skus > 0 ? eur(h.margin_overstated) : "—"}</div>
                <p className="mt-1 text-xs text-slate-600">{h.priced_skus > 0 ? t("essentials.marginSub", { priced: h.priced_skus, unpriced: h.unpriced_skus }) : t("essentials.marginNone")}</p>
              </div>
              <div className="rounded-lg border border-slate-200 p-4">
                <div className={label}>{t("essentials.demurrage")}</div>
                <div className="mt-1 text-2xl font-semibold figure-proportional">{eur(h.demurrage_paid)}</div>
                <p className="mt-1 text-xs text-slate-600">{t("essentials.demurrageSub", { count: audit.demurrage.filter((d) => Number(d.paid) > 0).length })}</p>
              </div>
            </div>
          </section>

          {/* 2 · The margin, per SKU, in euros over the period's volume. The table's scroll region is
              named apart from the section, so a screen reader's list of landmarks tells them apart. */}
          <section className={section} aria-labelledby="audit-margins">
            <h2 id="audit-margins" className={h2}>{t("margins.title")}</h2>
            <p className="mt-1 mb-3 text-sm text-slate-600">{priced ? t("margins.subtitle") : t("margins.subtitleUnpriced")}</p>
            {audit.margins.length === 0 ? (
              <p className="text-sm text-slate-600">{t("margins.none")}</p>
            ) : (
              <ScrollRegion label={t("table", { title: t("margins.title") })} className="rounded-lg border border-slate-200">
                <table className="min-w-full text-sm">
                  <thead className="bg-slate-50">
                    <tr>
                      <th className={thc}>{t("margins.th.sku")}</th>
                      <th className={`${thc} text-right`}>{t("margins.th.quantity")}</th>
                      <th className={`${thc} text-right`}>{t("margins.th.unitFob")}</th>
                      <th className={`${thc} text-right`}>{t("margins.th.unitLanded")}</th>
                      <th className={`${thc} text-right`}>{t("margins.th.approach")}</th>
                      {assumed !== null ? <th className={`${thc} text-right`}>{t("margins.th.gap", { assumed: ratio(assumed) })}</th> : null}
                      {priced ? (
                        <>
                          <th className={`${thc} text-right`}>{t("margins.th.salePrice")}</th>
                          <th className={`${thc} text-right`}>{t("margins.th.onFob")}</th>
                          <th className={`${thc} text-right`}>{t("margins.th.real")}</th>
                          <th className={`${thc} text-right`}>{t("margins.th.lost")}</th>
                        </>
                      ) : null}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {audit.margins.map((m) => (
                      <tr key={m.sku} className="print:break-inside-avoid">
                        <td className={tdc}><span className="font-mono">{m.sku}</span>{m.description ? <div className="text-xs text-slate-500">{m.description}</div> : null}</td>
                        <td className={`${tdc} text-right tabular-nums text-slate-600`}>{qty(m.quantity, locale)}</td>
                        <td className={`${tdc} text-right tabular-nums text-slate-600`}>{eur(m.unit_fob)}</td>
                        <td className={`${tdc} text-right tabular-nums`}>{eur(m.unit_landed)}</td>
                        <td className={`${tdc} text-right tabular-nums font-semibold`}>{eur(m.approach_costs)}</td>
                        {assumed !== null ? <td className={`${tdc} text-right tabular-nums ${Number(m.gap_vs_assumed ?? 0) > 0 ? "text-red-700" : "text-emerald-800"}`}>{m.gap_vs_assumed != null ? signed(m.gap_vs_assumed) : "—"}</td> : null}
                        {priced ? (
                          m.sale_price !== null && m.sale_price !== undefined ? (
                            <>
                              <td className={`${tdc} text-right tabular-nums text-slate-600`}>{eur(m.sale_price)}</td>
                              <td className={`${tdc} text-right tabular-nums text-slate-500`}>{m.margin_on_fob_pct != null ? pct1(m.margin_on_fob_pct) : "—"}</td>
                              <td className={`${tdc} text-right tabular-nums font-medium ${Number(m.margin_real_pct ?? 0) < 0 ? "text-red-700" : ""}`}>{m.margin_real_pct != null ? pct1(m.margin_real_pct) : "—"}</td>
                              <td className={`${tdc} text-right tabular-nums text-slate-600 whitespace-nowrap`}>{m.points_lost != null ? t("margins.points", { points: points(m.points_lost) }) : "—"}</td>
                            </>
                          ) : (
                            <td colSpan={4} className={`${tdc} text-xs text-slate-500`}>{t("margins.noPrice")}</td>
                          )
                        ) : null}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </ScrollRegion>
            )}
          </section>

          {/* 3 · What to have the providers justify: facts first, then questions. */}
          <section className={section} aria-labelledby="audit-claims">
            <h2 id="audit-claims" className={h2}>{t("claims.title")}</h2>
            <p className="mt-1 text-sm text-slate-600">{t("claims.subtitle")}</p>
            {claims.length === 0 ? <p className="mt-3 text-sm text-slate-600">{t("claims.none")}</p> : <ul className="mt-2 divide-y divide-slate-100">{claims.map(findingRow)}</ul>}
          </section>

          {/* 4 · Demurrage and detention: what was paid, and what it weighed on every unit in the box. */}
          <section className={section} aria-labelledby="audit-demurrage">
            <h2 id="audit-demurrage" className={h2}>{t("demurrage.title")}</h2>
            {audit.demurrage.length === 0 ? (
              <p className="mt-2 text-sm text-slate-600">{t("demurrage.none")}</p>
            ) : (
              <>
                <ScrollRegion label={t("table", { title: t("demurrage.title") })} className="mt-3 rounded-lg border border-slate-200">
                  <table className="min-w-full text-sm">
                    <thead className="bg-slate-50">
                      <tr>
                        <th className={thc}>{t("demurrage.th.container")}</th>
                        <th className={`${thc} text-right`}>{t("demurrage.th.paid")}</th>
                        <th className={`${thc} text-right`}>{t("demurrage.th.perUnit")}</th>
                        <th className={`${thc} text-right`}>{t("demurrage.th.late")}</th>
                        <th className={`${thc} text-right`}>{t("demurrage.th.avoided")}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {audit.demurrage.map((d) => (
                        <tr key={d.container_id} className="print:break-inside-avoid">
                          <td className={tdc}>{header.linkContainers ? <Link href={`/container/${d.container_id}`} className="font-mono text-blue-700 hover:underline">{d.container_number}</Link> : <span className="font-mono">{d.container_number}</span>}</td>
                          {/* A line can be here for what was avoided alone: nothing paid is a dash, not "0,00 € paid". */}
                      <td className={`${tdc} text-right tabular-nums font-medium`}>{Number(d.paid) > 0 ? eur(d.paid) : "—"}</td>
                          <td className={`${tdc} text-right tabular-nums text-slate-600`}>{d.per_unit != null ? t("demurrage.perUnit", { amount: money(d.per_unit, cur, locale), quantity: qty(d.quantity, locale) }) : "—"}</td>
                          <td className={`${tdc} text-right tabular-nums text-slate-600`}>{d.days_over != null && d.days_over > 0 ? t("demurrage.late", { days: d.days_over }) : "—"}</td>
                          <td className={`${tdc} text-right tabular-nums text-slate-600`}>
                            {d.avoided != null ? eur(d.avoided) : "—"}
                            {d.rule_code === "DND_AVOIDED_ESTIMATED" && d.days != null && d.daily_rate != null ? <div className="text-xs text-slate-500">{t("demurrage.avoidedHow", { days: d.days, rate: eur(d.daily_rate) })}</div> : null}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </ScrollRegion>
                <p className="mt-2 text-xs text-slate-500">
                  {t("demurrage.avoidedHint")}
                  {audit.demurrage.some((d) => d.rule_code === "DND_AVOIDED_NO_RATE") ? ` ${t("demurrage.noRateCard")}` : ""}
                </p>
              </>
            )}
          </section>

          {/* 5 · The landed cost ratio, where it comes from, against the one applied from memory. */}
          <section className={section} aria-labelledby="audit-coefficient">
            <h2 id="audit-coefficient" className={h2}>{t("coefficient.title")}</h2>
            <p className="mt-1 text-sm text-slate-600">
              {assumed === null ? t("coefficient.subtitleNoAssumed") : t("coefficient.subtitle", { assumed: ratio(assumed) })}
              {header.redacted ? ` ${t("coefficient.suppliersRemoved")}` : ""}
            </p>
            <div className="mt-3 grid gap-4 lg:grid-cols-2">
              {([
                ["month", audit.coefficient_by_month],
                ["supplier", audit.coefficient_by_supplier],
              ] as const).filter(([, rows]) => rows.length > 0).map(([kind, rows]) => (
                <ScrollRegion key={kind} label={`${t("coefficient.title")} · ${t(`coefficient.th.${kind}`)}`} className="rounded-lg border border-slate-200 print:break-inside-avoid">
                  <table className="min-w-full text-sm">
                    <thead className="bg-slate-50">
                      <tr>
                        <th className={thc}>{t(`coefficient.th.${kind}`)}</th>
                        <th className={`${thc} text-right`}>{t("coefficient.th.fob")}</th>
                        <th className={`${thc} text-right`}>{t("coefficient.th.landed")}</th>
                        <th className={`${thc} text-right`}>{t("coefficient.th.coef")}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {rows.map((r) => (
                        <tr key={r.key}>
                          <td className={tdc}>{kind === "month" ? (r.key === "unknown" ? t("coefficient.noDate") : monthTitle(r.key, locale)) : r.key === "unknown" ? t("coefficient.noSupplier") : r.label}</td>
                          <td className={`${tdc} text-right tabular-nums text-slate-600`}>{eur(r.fob)}</td>
                          <td className={`${tdc} text-right tabular-nums text-slate-600`}>{eur(r.landed)}</td>
                          <td className={`${tdc} text-right tabular-nums font-semibold whitespace-nowrap`}>
                            {above(r.coefficient) ? (
                              <>
                                <span className="mr-1.5 inline-block w-1.5 h-1.5 rounded-full bg-amber-500 align-middle" title={t("coefficient.above")} aria-hidden />
                                <span className="sr-only">{t("coefficient.above")} : </span>
                              </>
                            ) : null}
                            {ratio(r.coefficient)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </ScrollRegion>
              ))}
            </div>
          </section>

          {/* 6 · How far the figures can be trusted: what is still estimated, what could not be placed. */}
          <section className={section} aria-labelledby="audit-quality">
            <h2 id="audit-quality" className={h2}>{t("quality.title")}</h2>
            <dl className="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-2 text-sm">
              <div className="flex justify-between gap-3"><dt className="text-slate-600">{t("quality.containers")}</dt><dd className="tabular-nums font-medium">{c.containers}</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-slate-600">{t("quality.landed")}</dt><dd className="tabular-nums font-medium">{eur(c.landed)}</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-slate-600">{t("quality.estimated")}</dt><dd className="tabular-nums font-medium">{eur(c.estimated)}{c.estimated_share_pct != null ? <span className="text-slate-500 font-normal"> ({pct1(c.estimated_share_pct)})</span> : null}</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-slate-600">{t("quality.withoutCost")}</dt><dd className="tabular-nums font-medium">{c.containers_without_cost}</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-slate-600">{t("quality.toReview")}</dt><dd className="tabular-nums font-medium">{c.invoices_to_review}</dd></div>
              {c.containers_not_compared != null ? (
                <div className="flex justify-between gap-3"><dt className="text-slate-600">{t("quality.notCompared")}</dt><dd className="tabular-nums font-medium">{c.containers_not_compared}</dd></div>
              ) : null}
              {c.containers_without_date != null ? (
                <div className="flex justify-between gap-3"><dt className="text-slate-600">{t("quality.withoutDate")}</dt><dd className="tabular-nums font-medium">{c.containers_without_date}</dd></div>
              ) : null}
              <div className="flex justify-between gap-3">
                <dt className="text-slate-600">{t("quality.fx")}</dt>
                <dd className="text-right text-slate-700">
                  {Object.entries(c.fx_sources ?? {}).length === 0
                    ? "—"
                    : Object.entries(c.fx_sources as Record<string, number>)
                        .map(([source, count]) => t("quality.fxSource", { source: tfx.has(source) ? tfx(source) : source, count }))
                        .join(" · ")}
                </dd>
              </div>
            </dl>
            {quality.length > 0 ? (
              <>
                <h3 className="mt-5 text-sm font-semibold text-slate-900">{t("quality.findings")}</h3>
                <ul className="mt-1 divide-y divide-slate-100">{quality.map(findingRow)}</ul>
              </>
            ) : null}
            <h3 className="mt-5 text-sm font-semibold text-slate-900">{t("method.title")}</h3>
            <ul className="mt-2 list-disc pl-5 space-y-1 text-xs text-slate-600">
              {/* A rule is said only by the documents computed with it: links created before a rule
                  existed carry no flag for it, and keep the sentences of their day. */}
              <li>{rules.duty_explained_pct != null ? t("method.allocationDuty", { pct: plain(rules.duty_explained_pct) }) : t("method.allocation")}</li>
              <li>{t("method.duplicate", { tolerance: plain(rules.duplicate_tolerance_pct), never: types(rules.never_duplicate) })}</li>
              {rules.same_invoice_never_duplicate ? <li>{t("method.sameInvoice")}</li> : null}
              <li>{t("method.aboveQuote", { pct: plain(rules.above_quote_min_pct), amount: eur(rules.above_quote_min_amount) })}</li>
              <li>
                {t("method.outlier", { ratio: plain(rules.outlier_ratio), excess: eur(rules.outlier_min_excess), basis: rules.outlier_min_basis, days: rules.outlier_lookback_days, freight: rules.freight_window_days, notCompared: types(rules.not_compared) })}
                {rules.outlier_needs_route_and_size ? ` ${t("method.outlierUnknown")}` : null}
              </li>
              <li>{t("method.stale", { days: rules.estimate_stale_after_days })}</li>
              {rules.demurrage_by_arrival ? <li>{t("method.demurrageByArrival")}</li> : null}
              <li>{t("method.once")}</li>
            </ul>
          </section>
        </>
      )}

      <footer className="px-8 py-4 border-t border-slate-200 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
        <span>{t("footer")}</span>
        <span className="font-medium text-slate-700">FreightSight</span>
      </footer>
    </article>
  );
}
