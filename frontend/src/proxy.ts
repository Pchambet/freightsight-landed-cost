import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

/**
 * Optimistic redirects only. In @clerk/nextjs v7 the middleware is not the security boundary:
 * every page, layout, route handler and server action calls `auth.protect()` itself (see lib/auth.ts).
 *
 * That's what makes it safe to fail open here: a path this file doesn't recognize as one of the
 * protected app sections falls through untouched. A real (app) route still protects itself at
 * render time; a URL that matches no route at all reaches Next's own 404 (src/app/not-found.tsx)
 * instead of being bounced to /sign-in: an unknown link, /sitemap.xml and the per-page /opengraph-image files were all redirected
 * to sign-in before this change, which also broke sitemap indexing and social previews.
 */
const PUBLIC = [/^\/$/, /^\/robots\.txt$/, /^\/securite$/, /^\/design-partner$/, /^\/sign-in(\/.*)?$/, /^\/sign-up(\/.*)?$/, /^\/choose-organization(\/.*)?$/];

// Every top-level segment under src/app/(app): the only URLs where an unauthenticated visitor
// should be bounced to sign-in before Next.js even attempts a render.
const PROTECTED = [/^\/(alerts|api|audit|container|containers|cost-audit|imports|invoices|overview|periods|purchase-orders|reports|settings|skus|start)(\/.*)?$/];

export default clerkMiddleware(async (auth, req) => {
  const { pathname } = req.nextUrl;
  if (PUBLIC.some((r) => r.test(pathname))) return;
  if (!PROTECTED.some((r) => r.test(pathname))) return; // unknown or public: let Next.js route it

  const { isAuthenticated, sessionStatus, orgId, redirectToSignIn } = await auth();
  if (!isAuthenticated) {
    if (sessionStatus === "pending") {
      const url = req.nextUrl.clone();
      url.pathname = "/choose-organization";
      return NextResponse.redirect(url);
    }
    return redirectToSignIn({ returnBackUrl: req.url });
  }
  if (!orgId) {
    const url = req.nextUrl.clone();
    url.pathname = "/choose-organization";
    return NextResponse.redirect(url);
  }
});

export const config = {
  matcher: [
    // `/r/<token>` is a document for someone who has no account and no Clerk cookie: the middleware
    // stays out of it entirely, so a development instance does not bounce that visitor through its
    // handshake before showing the page.
    // The public marketing pages (the root, /securite, /design-partner) stay out too, and so do what
    // crawlers and link previews fetch around them: robots.txt, sitemap.xml and the Open Graph and
    // Twitter images. On a Clerk development instance the middleware answers any request that asks for
    // HTML without a Clerk cookie with a redirect to Clerk's handshake, and a client that keeps no
    // cookies — a search engine sends a browser's Accept — loops on it forever (measured on
    // production, 18 Sept 2026: ten 307s and no page, a 307 on robots.txt). None of these read a session.
    "/((?!_next|r/|securite$|design-partner$|$|[^?]*(?:opengraph|twitter)-image|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|txt|xml|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
    "/__clerk/(.*)",
  ],
};
