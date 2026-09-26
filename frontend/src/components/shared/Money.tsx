import { useLocale } from "next-intl";
import { money } from "@/lib/format";

export function Money({
  amount,
  currency,
  className,
}: {
  amount: string | number | null | undefined;
  currency: string;
  className?: string;
}) {
  const locale = useLocale();
  return <span className={`tabular-nums ${className ?? ""}`}>{money(amount, currency, locale)}</span>;
}
