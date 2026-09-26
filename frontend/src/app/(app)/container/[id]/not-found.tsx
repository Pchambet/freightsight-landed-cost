import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { Box } from "lucide-react";

export default async function ContainerNotFound() {
  const t = await getTranslations("container.notFound");
  return (
    <div className="flex items-center justify-center py-16">
      <div className="bg-white p-8 rounded-lg border border-slate-200 text-center max-w-md w-full">
        <div className="w-12 h-12 bg-slate-100 text-slate-600 rounded-full flex items-center justify-center mx-auto mb-4">
          <Box className="w-6 h-6" />
        </div>
        <h1 className="text-lg font-semibold mb-2">{t("title")}</h1>
        <p className="text-slate-500 mb-6 text-sm">{t("body")}</p>
        <Link href="/containers" className="inline-flex items-center justify-center bg-slate-900 text-white text-sm font-medium py-2 px-4 rounded-md hover:bg-slate-800 w-full">
          {t("back")}
        </Link>
      </div>
    </div>
  );
}
