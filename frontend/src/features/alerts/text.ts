import type { Schemas } from "@/lib/api/client";
import { dateShort, dateTimeShort, money } from "@/lib/format";

type Alert = Schemas["AlertResponse"];
type T = (key: string, values?: Record<string, string | number>) => string;

/**
 * The alert in the reader's language, built from its kind and payload. The API's own `title` and
 * `body` are English and only used for a kind this screen does not know, or a payload that lacks
 * what the sentence needs (alerts written before the payload carried it).
 *
 * This mirrors `backend/app/domain/alerts/text.py`, which writes the same alert for the e-mail
 * digest — `tests/test_alert_text.py` on the backend compares the two sets of sentences so they
 * cannot drift apart quietly. Keep the two files' branching in step.
 */
export function alertText(a: Alert, t: T, risk: (code: string) => string, locale: string): { title: string; body: string } {
  const p = (a.payload ?? {}) as Record<string, unknown>;
  const container = a.container_number ?? "";
  const str = (k: string) => (typeof p[k] === "string" ? (p[k] as string) : null);
  const num = (k: string) => (typeof p[k] === "number" ? (p[k] as number) : typeof p[k] === "string" && p[k] !== "" && !Number.isNaN(Number(p[k])) ? Number(p[k]) : null);
  const caveat = (body: string) => (p.port_of_discharge_confirmed === false ? `${body} ${t("text.DND_RISK.portUnconfirmed")}` : body);
  try {
    switch (a.kind) {
      case "DND_RISK": {
        const code = str("risk");
        if (!code || !container) break;
        const detention = str("clock") === "detention";
        const running = code === "INCURRING";
        const currency = str("currency");
        const totalStr = str("amount_to_date");
        const rateStr = str("daily_rate");
        const amount = currency && totalStr && rateStr ? money(totalStr, currency, locale) : null;
        const rate = currency && totalStr && rateStr ? money(rateStr, currency, locale) : null;
        const dateKey = detention ? "deadline" : "lfd";
        const when = str("deadline") ?? str("last_free_day");

        let title: string;
        if (running) {
          const suffix = amount ? "Amount" : "";
          const key = detention ? "titleDetentionRunning" : "titleRunning";
          title = t(`text.DND_RISK.${key}${suffix}`, { container, amount: amount ?? "" });
        } else {
          const key = detention ? "titleDetention" : "title";
          title = t(`text.DND_RISK.${key}`, { container, risk: risk(code) });
        }
        if (!when) return { title, body: t("text.DND_RISK.bodyNoLfd") };
        const printed = { [dateKey]: dateShort(when, locale) };

        if (running) {
          const elapsed = num("days_elapsed");
          if (elapsed === null) {
            const key = detention ? "bodyDetentionRunning" : "bodyRunning";
            return { title, body: caveat(t(`text.DND_RISK.${key}`, printed)) };
          }
          const days = Math.round(elapsed);
          const stem = detention ? "bodyDetentionRunningDays" : "bodyRunningDays";
          const body = t(`text.DND_RISK.${stem}`, { days, ...printed });
          const amountLine = amount && rate ? t("text.DND_RISK.amount", { amount, rate }) : t("text.DND_RISK.noRate");
          return { title, body: caveat(`${body} ${amountLine}`) };
        }

        const left = num("days_left");
        const days = left !== null ? Math.round(left) : Math.round((new Date(when).getTime() - Date.now()) / 86_400_000);
        if (days < 0) {
          const key = detention ? "bodyDetention" : "body";
          return { title, body: caveat(t(`text.DND_RISK.${key}`, printed)) };
        }
        const stem = detention ? "bodyDetentionLeft" : "bodyLeft";
        return { title, body: caveat(t(`text.DND_RISK.${stem}`, { days, ...printed })) };
      }
      case "ETA_CHANGED": {
        const hours = num("delta_hours");
        const eta = str("eta");
        if (hours === null || !eta || !container) break;
        const previous = str("previous_eta");
        return {
          title: t("text.ETA_CHANGED.title", { container, hours: Math.abs(Math.round(hours)), direction: t(hours > 0 ? "text.ETA_CHANGED.later" : "text.ETA_CHANGED.earlier") }),
          body: previous ? t("text.ETA_CHANGED.body", { eta: dateTimeShort(eta, locale), previous: dateTimeShort(previous, locale) }) : t("text.ETA_CHANGED.bodyNoPrevious", { eta: dateTimeShort(eta, locale) }),
        };
      }
      case "TRACKING_DATA_MISSING": {
        if (!container) break;
        const title = t("text.TRACKING_DATA_MISSING.title", { container });
        if (str("reason") === "MANUAL_NO_ENTRY") {
          const eta = str("eta");
          if (!eta) break;
          return { title, body: t("text.TRACKING_DATA_MISSING.bodyManual", { container, eta: dateShort(eta, locale) }) };
        }
        const provider = str("provider");
        const hours = num("hours");
        if (!provider || hours === null) break;
        return { title, body: t("text.TRACKING_DATA_MISSING.body", { provider, hours: Math.round(hours) }) };
      }
      case "TRACKING_PROVIDER_DOWN": {
        const provider = str("provider");
        if (!provider) break;
        return { title: t("text.TRACKING_PROVIDER_DOWN.title", { provider }), body: t("text.TRACKING_PROVIDER_DOWN.body", { provider }) };
      }
      case "TRACKING_DELIVERY_FAILED": {
        if (str("channel") === "email") {
          const attempts = num("attempts");
          const reason = str("reason");
          if (attempts === null || !reason) break;
          return { title: t("text.NOTIFICATION_FAILED.title"), body: t("text.NOTIFICATION_FAILED.body", { attempts: Math.round(attempts), reason }) };
        }
        const provider = str("provider");
        const attempts = num("attempts");
        const received = str("received_at");
        if (!provider || attempts === null || !received || !container) break;
        return {
          title: t("text.TRACKING_DELIVERY_FAILED.title", { container }),
          body: t("text.TRACKING_DELIVERY_FAILED.body", { provider, received: dateTimeShort(received, locale), attempts: Math.round(attempts) }),
        };
      }
      default:
        break;
    }
  } catch {
    // a message key or a value the formatter refuses: the API's sentence is better than a crash
  }
  return { title: a.title, body: a.body };
}
