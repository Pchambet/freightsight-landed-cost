import { getRequestConfig } from "next-intl/server";
import { isLocale } from "./config";
import { getUserLocale } from "./locale";

/**
 * The visitor's locale by default. But a document written for someone else — a shared landed cost
 * report, a shared audit — is rendered in its issuer's language, asked for explicitly with
 * `getTranslations({ locale })`: that explicit locale wins, or the words would follow the reader's
 * browser while the numbers followed the issuer.
 */
export default getRequestConfig(async ({ locale: explicit }) => {
  const locale = isLocale(explicit) ? explicit : await getUserLocale();
  return { locale, messages: (await import(`../../messages/${locale}.json`)).default };
});
