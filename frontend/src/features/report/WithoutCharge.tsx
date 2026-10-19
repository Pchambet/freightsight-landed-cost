import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { Info } from "lucide-react";
import { money } from "@/lib/format";

/** What the API says about the containers a landed figure leaves out: how many in all
 * (`containers_without_charge`), how many of those have charges on file that cannot be placed yet
 * (`containers_held`), the orders with no exchange rate that hold them (`held_orders`), and, where
 * the API gives it, the known value of their goods and how many of their lines have none. */
export type LeftOut = {
  containers_without_charge?: number | null;
  containers_held?: number | null;
  held_orders?: string[] | null;
  uncharged_fob?: string | null;
  uncharged_fob_missing_lines?: number | null;
};

/** The order to send a reader to for a held container: the one order holding it, by id, when the
 * page knows it (`unrated`: the orders with no rate, as the API names them). */
export function heldRate(of: LeftOut | null | undefined, unrated: { id: string; po_number: string }[] | null | undefined): { id: string; po_number: string } | null {
  const orders = of?.held_orders ?? [];
  return orders.length === 1 ? (unrated ?? []).find((o) => o.po_number === orders[0]) ?? null : null;
}

/** The orders as one ICU pair: `orderCount` picks the sentence, `orders` names them. */
export const heldParams = (orders: string[] | null | undefined) => ({ orderCount: orders?.length ?? 0, orders: (orders ?? []).join(", ") });

/**
 * Why a landed figure counts fewer containers than the FOB beside it. Two different reasons, never
 * one sentence for both: a container with no charge at all, estimated or invoiced, and one whose
 * charges are on file but cannot be placed yet (most often an order with no exchange rate). Telling
 * the second as the first reads as "the forwarder's invoice was never entered", and invites a person
 * to enter it twice. The API leaves both out of every landed figure rather than pass their FOB off as
 * a landed cost, and their goods out of the purchase price those figures are set against; this says
 * so next to the figures, with the known value of the goods left out when the API gives it.
 *
 * The link goes where the thing is fixed: the rate of the one order holding the charges (`rate`),
 * else the containers holding them, in every view (a delivered box is not in the default one).
 */
export default function WithoutCharge({ of, href = "/containers", rate = null, currency, className = "" }: { of: LeftOut | null | undefined; href?: string | null; rate?: { id: string; po_number: string } | null; currency?: string; className?: string }) {
  const t = useTranslations("withoutCharge");
  const locale = useLocale();
  const all = of?.containers_without_charge ?? 0;
  const held = Math.min(of?.containers_held ?? 0, all);
  const bare = all - held;
  if (!all) return null;
  const missing = of?.uncharged_fob_missing_lines ?? 0;
  const goods = currency && of?.uncharged_fob != null && (Number(of.uncharged_fob) > 0 || missing > 0) ? t("goods", { missing, fob: money(of.uncharged_fob, currency, locale) }) : null;
  const sentences = [bare > 0 ? t("total", { count: bare }) : null, held > 0 ? t("held", { count: held, ...heldParams(of?.held_orders) }) : null, goods].filter(Boolean).join(" ");
  const orders = of?.held_orders ?? [];
  const link = href === null ? null : held > 0 && rate ? { href: `/purchase-orders/${rate.id}#taux`, label: t("setRate", { order: rate.po_number }) } : held > 0 ? { href: orders.length === 1 ? `/containers?view=all&q=${encodeURIComponent(orders[0])}` : "/containers?view=all", label: t("see") } : { href, label: t("see") };
  return (
    <p role="note" className={`flex items-start gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700 ${className}`}>
      <Info className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" aria-hidden />
      <span>
        {sentences}
        {link ? (
          <>
            {" "}
            <Link href={link.href} className="font-medium underline underline-offset-2 hover:no-underline print:no-underline">{link.label}</Link>
          </>
        ) : null}
      </span>
    </p>
  );
}

/** The containers left out, in a few words: those with no charge, those with charges held. */
export function leftOutText(t: (key: "count" | "heldCount", values: { count: number }) => string, of: LeftOut | null | undefined): string | null {
  const all = of?.containers_without_charge ?? 0;
  const held = Math.min(of?.containers_held ?? 0, all);
  if (!all) return null;
  return [all > held ? t("count", { count: all - held }) : null, held > 0 ? t("heldCount", { count: held }) : null].filter(Boolean).join(" · ");
}

/** The same under a row: how many of its containers were left out and why (`of`), or, for one
 * container or one of its lines (`box`), whether it has no charge at all or charges held. */
export function WithoutChargeLine({ of, box }: { of?: LeftOut | null; box?: { without_charge?: boolean | null; charges_held?: boolean | null; held_orders?: string[] | null } }) {
  const t = useTranslations("withoutCharge");
  if (box) {
    if (!box.without_charge) return null;
    return <span className="block text-xs font-normal text-slate-500">{box.charges_held ? t("heldBox", heldParams(box.held_orders)) : t("box")}</span>;
  }
  const text = leftOutText(t, of);
  return text ? <span className="block text-xs font-normal text-slate-500">{text}</span> : null;
}
