import "server-only";
import { auth } from "@clerk/nextjs/server";
import { headers } from "next/headers";
import { redirect } from "next/navigation";

/**
 * The security boundary for pages and layouts: signed-out users go to sign-in, users without an
 * active organization go to the picker. Returns the Clerk auth object for the request.
 */
export async function requireAuth() {
  const a = await auth();
  if (!a.isAuthenticated) {
    if (a.sessionStatus === "pending") redirect("/choose-organization");
    redirect("/sign-in");
  }
  if (!a.orgId) redirect("/choose-organization");
  return a;
}

/** Must stay equal to `TAB_ORG_HEADER` in components/layout/OrgGuard.tsx (a client file this one cannot import). */
const TAB_ORG_HEADER = "x-fs-tab-org";

/**
 * For Server Actions and Route Handlers: 401/404 instead of a redirect.
 *
 * It also refuses to act for a company the tab is not looking at. The session cookie is one for
 * every tab of the browser, the company on screen is per tab: a user with two companies open can
 * click "add a cost" under company B while the cookie says company A, and an action that names no
 * record (create a container, import a file, load the sample data) would then write into A. The
 * tab says which company it shows (OrgGuard sets the header on every request it sends here); when
 * that and the verified session disagree, nothing is written and the user lands on the overview
 * with the explanation. No header — a request made before the guard mounted — is let through:
 * this is a belt against an accident, the API's own tenancy checks remain the lock.
 */
export async function protectAction(): Promise<void> {
  const [a, h] = await Promise.all([auth.protect(), headers()]);
  const tabOrg = h.get(TAB_ORG_HEADER);
  if (tabOrg && a.orgId && tabOrg !== a.orgId) redirect("/overview?notice=org-changed");
}
