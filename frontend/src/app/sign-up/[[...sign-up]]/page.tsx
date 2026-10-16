import { SignUp } from "@clerk/nextjs";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

export default async function SignUpPage() {
  const t = await getTranslations("auth.signUp");
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 p-6">
      <SignUp />
      {/* GDPR art. 13: the person is told how their data is processed at the moment it is collected.
          The policy exists in French only, so both languages link the same page. */}
      <p className="max-w-sm text-center text-xs text-slate-500">
        {t.rich("privacy", {
          link: (chunks) => (
            <Link href="/confidentialite" className="underline hover:text-slate-700">
              {chunks}
            </Link>
          ),
        })}
      </p>
    </div>
  );
}
