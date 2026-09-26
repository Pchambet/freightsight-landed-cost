import { getTranslations } from "next-intl/server";
import { CloudOff } from "lucide-react";
import { problemMessage } from "@/lib/api/client";

/**
 * What a page shows when the API did not answer it. Never an empty state: "you have no containers"
 * and "we could not read your containers" are opposite sentences, and the second one said as the
 * first sends a user off to re-import data that is sitting safely in the database.
 */
export default async function ApiErrorNotice({ error, retryHref }: { error: unknown; retryHref: string }) {
  const [t, message] = await Promise.all([getTranslations("common"), problemMessage(error)]);
  return (
    <div role="alert" className="rounded-xl border border-red-200 bg-red-50 px-5 py-4 mb-6 flex items-start gap-3">
      <CloudOff className="w-5 h-5 mt-0.5 shrink-0 text-red-600" aria-hidden />
      <div className="min-w-0 text-sm">
        <p className="font-medium text-red-900">{t("dataUnavailable")}</p>
        <p className="mt-0.5 text-red-800">{message}</p>
        {/* A plain link on purpose: a full request, no client cache between the user and the retry. */}
        <a href={retryHref} className="mt-2 inline-block font-medium text-red-900 underline underline-offset-2 hover:no-underline">{t("retry")}</a>
      </div>
    </div>
  );
}
