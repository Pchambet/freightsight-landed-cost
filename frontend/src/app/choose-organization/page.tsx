import type { Metadata } from "next";
import { OrganizationList, TaskChooseOrganization } from "@clerk/nextjs";
import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import { getTranslations } from "next-intl/server";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("auth.chooseOrganization");
  return { title: t("title") };
}

/**
 * Two cases land here: a freshly signed-in user whose session is `pending` (Clerk's
 * choose-organization task), and an active user with no active organization.
 */
export default async function ChooseOrganizationPage() {
  const [a, t] = await Promise.all([auth(), getTranslations("auth.chooseOrganization")]);
  if (a.isAuthenticated && a.orgId) redirect("/overview");
  if (!a.isAuthenticated && a.sessionStatus !== "pending") redirect("/sign-in");

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="space-y-4">
        <div className="text-center">
          <h1 className="text-lg font-semibold">{t("title")}</h1>
          <p className="text-sm text-slate-500">{t("body")}</p>
        </div>
        {a.sessionStatus === "pending" ? (
          <TaskChooseOrganization redirectUrlComplete="/overview" />
        ) : (
          <OrganizationList hidePersonal afterSelectOrganizationUrl="/overview" afterCreateOrganizationUrl="/overview" />
        )}
      </div>
    </div>
  );
}
