"use client";

import { useLocale, useTranslations } from "next-intl";
import { MAX_UPLOAD_BYTES, fileSize } from "@/lib/uploads";

/**
 * Said where the file was chosen, before anything is sent: a file over the limit never leaves the
 * browser. What to do instead depends on what the file is — the forwarder's own PDF is lighter than a
 * scan of it; an import file can be sent in two parts.
 */
export default function FileTooLarge({ id, file, kind }: { id: string; file: File; kind: "invoice" | "import" }) {
  const t = useTranslations("common.upload");
  const locale = useLocale();
  return (
    <p id={id} role="alert" className="text-sm text-red-700">
      {t("tooLarge", { size: fileSize(file.size, locale), max: fileSize(MAX_UPLOAD_BYTES, locale) })}{" "}
      {t(kind === "invoice" ? "tooLargeInvoice" : "tooLargeImport")}
    </p>
  );
}
