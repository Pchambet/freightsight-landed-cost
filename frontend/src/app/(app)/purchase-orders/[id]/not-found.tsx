import Link from "next/link";
import { getTranslations } from "next-intl/server";

export default async function PurchaseOrderNotFound() {
  const t = await getTranslations("purchaseOrder.notFound");
  return (
    <div className="flex items-center justify-center py-16">
      <div className="bg-white p-8 rounded-lg border border-slate-200 text-center max-w-md w-full">
        <h1 className="text-lg font-semibold mb-2">{t("title")}</h1>
        <p className="text-slate-500 mb-6 text-sm">{t("body")}</p>
        <Link href="/purchase-orders" className="inline-flex items-center justify-center bg-slate-900 text-white text-sm font-medium py-2 px-4 rounded-md hover:bg-slate-800 w-full">{t("back")}</Link>
      </div>
    </div>
  );
}
