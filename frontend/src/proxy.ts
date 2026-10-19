import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse, type NextFetchEvent, type NextRequest } from "next/server";

/**
 * Optimistic redirects only. In @clerk/nextjs v7 the middleware is not the security boundary:
 * every page, layout, route handler and server action calls `auth.protect()` itself (see lib/auth.ts).
 *
 * That's what makes it safe to fail open here: a path this file doesn't recognize as one of the
 * protected app sections falls through untouched. A real (app) route still protects itself at
 * render time; a URL that matches no route at all reaches Next's own 404 (src/app/not-found.tsx)
 * instead of being bounced to /sign-in: an unknown link, /sitemap.xml and the per-page
 * /opengraph-image files were all redirected to sign-in before, which also broke sitemap indexing and
 * social previews.
 */
const PUBLIC = [/^\/$/, /^\/robots\.txt$/, /^\/securite$/, /^\/design-partner$/, /^\/confidentialite$/, /^\/mentions-legales$/, /^\/cgv$/, /^\/sign-in(\/.*)?$/, /^\/sign-up(\/.*)?$/, /^\/choose-organization(\/.*)?$/];

// Every top-level segment under src/app/(app): the only URLs where an unauthenticated visitor
// should be bounced to sign-in before Next.js even attempts a render.
const PROTECTED = [/^\/(alerts|api|audit|container|containers|cost-audit|imports|invoices|overview|periods|purchase-orders|reports|settings|skus|start)(\/.*)?$/];

const clerk = clerkMiddleware(async (auth, req) => {
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

/**
 * Paths the Clerk middleware stays out of. `/r/<token>` is a document for someone who has no account
 * and no Clerk cookie, so a development instance must not bounce that visitor through its handshake
 * before showing the page. The public marketing and legal pages (the root, /securite,
 * /design-partner, /confidentialite, /mentions-legales, /cgv) stay out too, and so do the Open Graph
 * and Twitter images link previews fetch (robots.txt and sitemap.xml never reach this file, see the
 * matcher). On a Clerk development instance the middleware answers any request that asks for HTML
 * without a Clerk cookie with a redirect to Clerk's handshake, and a client that keeps no cookies — a
 * search engine sends a browser's Accept — loops on it forever (measured on production, 18 Sept 2026:
 * ten 307s and no page, a 307 on robots.txt). None of these read a session.
 */
const WITHOUT_CLERK = /^\/(?:r\/|(?:securite|design-partner|confidentialite|mentions-legales|cgv)?$|[^?]*(?:opengraph|twitter)-image)/;

// Route handlers answer with JSON and files (an invoice PDF shown in an iframe), not pages: the policy
// is for documents that run the app's scripts.
const ROUTE_HANDLER = /^\/(?:api|trpc|__clerk)(?:\/|$)/;

/** The Frontend API host encoded in the publishable key ("pk_live_" + base64 of "clerk.example.com$"). */
function clerkFrontendApi(): string | null {
  const encoded = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY?.split("_")[2];
  try {
    return encoded ? `https://${atob(encoded).replace(/\$$/, "")}` : null;
  } catch {
    return null;
  }
}

/** Where the browser SDK sends errors: the DSN's origin, without its key. */
function sentryOrigin(): string | null {
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
  try {
    return dsn ? new URL(dsn).origin : null;
  } catch {
    return null;
  }
}

/**
 * A nonce-based policy: only the scripts Next.js and Clerk render with this request's nonce run, and
 * what they load in turn ('strict-dynamic': clerk-js, then Cloudflare Turnstile). Every page is
 * rendered per request (the root layout is force-dynamic), which is what lets Next.js put the nonce on
 * its own scripts. The other sources are those Clerk documents for its components
 * (clerk.com/docs/guides/secure/best-practices/csp-headers) minus Stripe and Google Maps, which this
 * app does not use, plus Sentry's ingest host. Styles stay 'unsafe-inline': Clerk's components and
 * the toasts inject <style> elements at runtime.
 */
function contentSecurityPolicy(nonce: string): string {
  const dev = process.env.NODE_ENV === "development";
  const connect = ["'self'", clerkFrontendApi(), "https://clerk-telemetry.com", "https://*.clerk-telemetry.com", sentryOrigin()];
  return [
    "default-src 'self'",
    // React rebuilds server error stacks with eval in development only.
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: https://img.clerk.com",
    "font-src 'self'",
    `connect-src ${connect.filter(Boolean).join(" ")}`,
    "frame-src 'self' https://challenges.cloudflare.com",
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    // 'self', not 'none': an invoice page shows the invoice's document in an iframe of its own origin.
    "frame-ancestors 'self'",
  ].join("; ");
}

/**
 * Passes headers on to the render as if the browser had sent them, on a response that may already
 * carry overrides (Clerk's auth state). The same `x-middleware-request-*` mechanism
 * NextResponse.next({ request: { headers } }) uses, which Clerk's middleware also writes to.
 */
function forwardRequestHeaders(res: Response, req: NextRequest, extra: Record<string, string>) {
  const OVERRIDE = "x-middleware-override-headers";
  if (!res.headers.get(OVERRIDE)) {
    res.headers.set(OVERRIDE, [...req.headers.keys()].join(","));
    req.headers.forEach((value, key) => res.headers.set(`x-middleware-request-${key}`, value));
  }
  for (const [key, value] of Object.entries(extra)) {
    res.headers.set(OVERRIDE, `${res.headers.get(OVERRIDE)},${key}`);
    res.headers.set(`x-middleware-request-${key}`, value);
  }
}

export default async function proxy(req: NextRequest, event: NextFetchEvent) {
  const { pathname } = req.nextUrl;
  const handler = ROUTE_HANDLER.test(pathname);
  // A route handler always goes through Clerk, even one whose path ends like an Open Graph image:
  // without the middleware its auth() throws, a 500 instead of the sign-in redirect.
  const res = !handler && WITHOUT_CLERK.test(pathname) ? NextResponse.next() : ((await clerk(req, event)) ?? NextResponse.next());
  if (handler || res.headers.has("location")) return res;

  const nonce = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(16))));
  const policy = contentSecurityPolicy(nonce);
  // Next.js reads the nonce from the request's policy and puts it on its own scripts; the root layout
  // hands x-nonce to Clerk's script tag.
  forwardRequestHeaders(res, req, { "content-security-policy": policy, "x-nonce": nonce });
  res.headers.set("Content-Security-Policy", policy);
  return res;
}

export const config = {
  matcher: [
    // Every page, so every page gets the policy; static files and Next's own assets do not need it.
    // robots.txt, sitemap.xml and other files with an extension are left out like those.
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|txt|xml|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
    "/__clerk/(.*)",
  ],
};
