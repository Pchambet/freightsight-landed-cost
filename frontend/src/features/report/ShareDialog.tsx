"use client";

import { useCallback, useEffect, useRef, useState, useTransition } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Check, Copy, Link2, Mail, Send, X } from "lucide-react";
import { toast } from "sonner";
import { btnPrimary, btnSecondary, input, label } from "@/components/layout/AppShell";
import { createShare, listShares, revokeShare, type ShareSubject, type ShareSummary } from "@/features/actions";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { dateShort, dateTimeShort, money } from "@/lib/format";

/**
 * Send the report to someone who has no account: a read-only link to a snapshot frozen at the moment
 * it is created. The token is shown once — the server keeps only its hash — so the link is put in
 * front of the sender at once, with a copy button and an e-mail ready to go. Underneath, the links
 * already out: how often each was opened, and a way to withdraw it.
 */
export default function ShareDialog({ subject, subjectLabel, currency, estimatedOnly }: { subject: ShareSubject; subjectLabel: string; currency: string; estimatedOnly: boolean }) {
  const t = useTranslations("share");
  const locale = useLocale();
  const [open, setOpen] = useState(false);
  const [days, setDays] = useState(30);
  const [redact, setRedact] = useState(false);
  const [created, setCreated] = useState<{ url: string; expiresAt: string; at: string; landed: string | null } | null>(null);
  // An audit covers a period and its links are listed across periods: each row names its own.
  const isAudit = subject.kind === "audit";
  const [copied, setCopied] = useState(false);
  const [shares, setShares] = useState<ShareSummary[] | null>(null);
  const [pending, start] = useTransition();
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  useFocusTrap(open, panel, trigger);

  const refresh = useCallback(() => {
    void listShares(subject).then((r) => setShares(r.ok ? r.data : []));
  }, [subject]);

  useEffect(() => {
    if (!open) return;
    refresh();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, refresh]);

  const copy = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error(t("copyFailed"));
    }
  };
  const mailto = created
    ? `mailto:?subject=${encodeURIComponent(t(isAudit ? "mail.auditSubject" : "mail.subject", { label: subjectLabel }))}&body=${encodeURIComponent(t(isAudit ? "mail.auditBody" : "mail.body", { label: subjectLabel, url: created.url, date: dateShort(created.expiresAt, locale) }))}`
    : "#";

  return (
    <>
      <button ref={trigger} type="button" onClick={() => setOpen(true)} className={btnPrimary}>
        <Send className="w-4 h-4 mr-1.5" aria-hidden />
        {t(subject.kind === "audit" ? "openAudit" : "open")}
      </button>
      {open ? (
        <div className="fixed inset-0 z-[70] flex items-start justify-center p-4 pt-[10vh] print:hidden">
          <button type="button" tabIndex={-1} className="absolute inset-0 bg-navy-950/50" aria-label={t("close")} onClick={() => setOpen(false)} />
          <div ref={panel} role="dialog" aria-modal="true" aria-labelledby="share-title" className="relative w-full max-w-lg rounded-xl bg-white shadow-pop ring-1 ring-slate-900/10 animate-rise">
            <div className="flex items-start justify-between gap-3 px-5 pt-5">
              <div>
                <h2 id="share-title" className="text-base font-semibold text-slate-900">{t(isAudit ? "auditTitle" : "title", { label: subjectLabel })}</h2>
                <p className="mt-1 text-sm text-slate-600">{t(isAudit ? "introAudit" : "intro")}</p>
              </div>
              <button type="button" onClick={() => setOpen(false)} className="p-1.5 -mr-1.5 rounded-md text-slate-500 hover:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
                <X className="w-4 h-4" aria-hidden />
                <span className="sr-only">{t("close")}</span>
              </button>
            </div>

            <div className="px-5 py-4">
              {created ? (
                <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4">
                  <p className="text-sm font-medium text-emerald-950">{isAudit
                      ? `${t("createdAudit", { when: dateTimeShortLocal(created.at, locale) })}${created.landed !== null ? ` · ${t("createdAuditLanded", { landed: money(created.landed, currency, locale) })}` : ""}`
                      : t("created", { when: dateTimeShortLocal(created.at, locale), landed: created.landed !== null ? money(created.landed, currency, locale) : "—" })}</p>
                  <div className="mt-3 flex gap-2">
                    <input readOnly value={created.url} aria-label={t("linkLabel")} onFocus={(e) => e.currentTarget.select()} className={`${input} font-mono text-xs`} />
                    <button type="button" onClick={copy} className={`${btnSecondary} shrink-0`}>
                      {copied ? <Check className="w-4 h-4 mr-1.5 text-emerald-600" aria-hidden /> : <Copy className="w-4 h-4 mr-1.5" aria-hidden />}
                      {copied ? t("copied") : t("copy")}
                    </button>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
                    <a href={mailto} className="inline-flex items-center gap-1.5 font-medium text-emerald-950 underline underline-offset-2 hover:no-underline"><Mail className="w-4 h-4" aria-hidden />{t("mail.action")}</a>
                    <span className="text-xs text-emerald-900">{t("shownOnce")}</span>
                  </div>
                </div>
              ) : (
                <>
                  {estimatedOnly ? <p className="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">{t("estimatedOnly")}</p> : null}
                  <div className="flex flex-wrap items-end gap-3">
                    <div>
                      <label className={label} htmlFor="share-days">{t("duration")}</label>
                      <select id="share-days" value={days} onChange={(e) => setDays(Number(e.target.value))} className={`${input} !w-auto`}>
                        {[7, 30, 90].map((d) => <option key={d} value={d}>{t("days", { days: d })}</option>)}
                      </select>
                    </div>
                    <button
                      type="button"
                      disabled={pending}
                      className={btnPrimary}
                      onClick={() =>
                        start(async () => {
                          const r = await createShare(subject, days, redact);
                          if (!r.ok) return void toast.error(r.error);
                          // The figure and the time are the server's: what was frozen, not what this screen showed.
                          setCreated({ url: `${window.location.origin}${r.data.path}`, expiresAt: r.data.expiresAt, at: r.data.generatedAt, landed: r.data.landed });
                          refresh();
                        })
                      }
                    >
                      <Link2 className="w-4 h-4 mr-1.5" aria-hidden />
                      {pending ? t("creating") : t("create")}
                    </button>
                  </div>
                  <label className="mt-4 flex items-start gap-2.5 text-sm text-slate-700">
                    <input type="checkbox" className="mt-0.5" checked={redact} onChange={(e) => setRedact(e.target.checked)} />
                    <span>
                      {t(isAudit ? "redactAudit" : "redact")}
                      <span className="block text-xs text-slate-500">{t(isAudit ? "redactAuditHint" : "redactHint")}</span>
                    </span>
                  </label>
                  <p className="mt-3 text-xs text-slate-500">{t(isAudit ? "frozenHintAudit" : "frozenHint")}</p>
                </>
              )}
            </div>

            <div className="border-t border-slate-200 px-5 py-4">
              <h3 className="text-xs font-medium uppercase tracking-wider text-slate-500">{t("existing")}</h3>
              {shares === null ? (
                <p className="mt-2 text-sm text-slate-500">{t("loading")}</p>
              ) : shares.length === 0 ? (
                <p className="mt-2 text-sm text-slate-500">{t("none")}</p>
              ) : (
                <ul className="mt-2 divide-y divide-slate-100 max-h-56 overflow-y-auto">
                  {shares.map((s) => (
                    <li key={s.id} className="py-2 flex items-center gap-3 text-sm">
                      <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${s.status === "active" ? "bg-emerald-50 text-emerald-800 ring-emerald-600/20" : "bg-slate-100 text-slate-600 ring-slate-500/20"}`}>{t(`status.${s.status === "active" || s.status === "expired" || s.status === "revoked" ? s.status : "expired"}`)}</span>
                      <span className="min-w-0 flex-1 text-slate-700">
                        {isAudit ? <span className="font-medium text-slate-900">{auditLabel(s.subject_label, locale)} · </span> : null}
                        {t("row", { created: dateShort(s.created_at, locale), expires: dateShort(s.expires_at, locale) })}
                        <span className="block text-xs text-slate-500">{s.view_count === 0 ? t("neverOpened") : t("opened", { count: s.view_count, when: s.last_viewed_at ? dateTimeShort(s.last_viewed_at, locale) : "—" })}</span>
                      </span>
                      {s.status === "active" ? (
                        <button
                          type="button"
                          disabled={pending}
                          className="shrink-0 text-sm text-red-700 hover:underline disabled:opacity-50"
                          onClick={() =>
                            start(async () => {
                              const r = await revokeShare(s.id);
                              if (!r.ok) return void toast.error(r.error);
                              toast.success(t("revoked"));
                              refresh();
                            })
                          }
                        >
                          {t("revoke")}
                        </button>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

/** An audit link is labelled "<from>..<to>" by the API: written as two dates. */
function auditLabel(label: string, locale: string): string {
  const [from, to] = label.split("..");
  return from && to ? `${dateShort(from, locale)} – ${dateShort(to, locale)}` : label;
}

/** The moment the snapshot was taken, in the sender's own time zone (this runs in their browser). */
function dateTimeShortLocal(iso: string, locale: string): string {
  return new Intl.DateTimeFormat(locale === "fr" ? "fr-FR" : "en-GB", { dateStyle: "short", timeStyle: "short" }).format(new Date(iso));
}
