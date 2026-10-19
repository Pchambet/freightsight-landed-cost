import { expect, test, type Page } from "@playwright/test";

/**
 * The Content-Security-Policy set in src/proxy.ts, checked on a production build: every page sends it,
 * with a fresh nonce, and loading the page breaks none of its rules. Offline like public-pages.spec.ts:
 * requests that are not the local server are aborted after the browser has checked them against the
 * policy, so a script or a connection the policy refuses still shows up as a violation here.
 */

test.beforeEach(async ({ page, context, baseURL }) => {
  // Skips a Clerk development instance's handshake (see public-pages.spec.ts).
  await context.addCookies([{ name: "__clerk_db_jwt", value: "e2e_stub", url: baseURL! }]);
  await page.route("**/*", (route) => {
    const host = new URL(route.request().url()).hostname;
    return host === "127.0.0.1" || host === "localhost" ? route.continue() : route.abort();
  });
});

/** Records every violation the page reports, from its first script on, and every console line about one. */
async function watchViolations(page: Page) {
  const logged: string[] = [];
  page.on("console", (m) => {
    if (/Content Security Policy|Content-Security-Policy/i.test(m.text())) logged.push(m.text());
  });
  await page.addInitScript(() => {
    const seen: string[] = [];
    (window as unknown as { __csp: string[] }).__csp = seen;
    document.addEventListener("securitypolicyviolation", (e) => seen.push(`${e.effectiveDirective} blocked ${e.blockedURI || "inline"}`));
  });
  return async () => [...logged, ...(await page.evaluate(() => (window as unknown as { __csp: string[] }).__csp))];
}

const PAGES = ["/?preview=1", "/securite", "/design-partner", "/confidentialite", "/sign-in", "/sign-up", "/this-does-not-exist", `/r/${"x".repeat(43)}`];

for (const path of PAGES) {
  test(`${path} sends a nonce-based policy and breaks none of its rules`, async ({ page }) => {
    const violations = await watchViolations(page);
    const response = await page.goto(path);
    const policy = response?.headers()["content-security-policy"] ?? "";
    expect(policy).toMatch(/script-src 'self' 'nonce-[A-Za-z0-9+/=]{24}' 'strict-dynamic'/);
    await page.waitForLoadState("networkidle");
    // The app has hydrated: Next's scripts ran under the policy, not merely arrived.
    expect(await page.evaluate(() => document.querySelectorAll("script").length)).toBeGreaterThan(0);
    expect(await violations()).toEqual([]);
  });
}

test("the policy a security questionnaire asks about, a new nonce on every request", async ({ request }) => {
  const policies: string[] = [];
  for (const path of ["/", "/", "/sign-in"]) {
    const response = await request.get(path, { maxRedirects: 0 });
    expect(response.status(), path).toBe(200);
    policies.push(response.headers()["content-security-policy"]);
  }
  const directives = Object.fromEntries(policies[0].split("; ").map((d) => [d.split(" ")[0], d.split(" ").slice(1).join(" ")]));
  expect(directives).toMatchObject({
    "default-src": "'self'",
    "object-src": "'none'",
    "base-uri": "'self'",
    "form-action": "'self'",
    "frame-ancestors": "'self'",
    "frame-src": "'self' https://challenges.cloudflare.com",
  });
  expect(directives["script-src"]).not.toContain("unsafe-inline");
  expect(directives["script-src"]).not.toContain("unsafe-eval");
  // Clerk's Frontend API, from the publishable key in playwright.config.ts.
  expect(directives["connect-src"]).toContain("https://clerk.example.com");
  const nonces = policies.map((p) => p.match(/'nonce-([^']+)'/)?.[1]);
  expect(new Set(nonces).size).toBe(3);
});

test("Next's and Clerk's script tags carry this response's nonce", async ({ page }) => {
  const response = await page.goto("/sign-in");
  const nonce = response?.headers()["content-security-policy"].match(/'nonce-([^']+)'/)?.[1];
  const scripts = await page.evaluate(() => [...document.querySelectorAll("script")].map((s) => ({ src: s.src, nonce: s.nonce })));
  expect(scripts.some((s) => s.src.includes("clerk.example.com"))).toBe(true);
  for (const s of scripts) expect(s.nonce, s.src || "inline script").toBe(nonce);
});

test("injected markup cannot run script, and the refusal is seen", async ({ page }) => {
  const violations = await watchViolations(page);
  await page.goto("/securite");
  // What an XSS looks like once it reaches the DOM: an inline handler, which no nonce can cover.
  await page.evaluate(() => document.body.insertAdjacentHTML("beforeend", '<img src="/nothing.png" onerror="window.__injected = true">'));
  await expect.poll(violations).toContainEqual("script-src-attr blocked inline");
  expect(await page.evaluate(() => (window as unknown as { __injected?: boolean }).__injected)).toBeUndefined();
});

test("files and redirects carry no page policy", async ({ request }) => {
  // Files are not pages, and a redirect renders nothing.
  for (const path of ["/robots.txt", "/sitemap.xml"]) {
    const response = await request.get(path);
    expect(response.headers()["content-security-policy"], path).toBeUndefined();
  }
  const redirect = await request.get("/containers", { maxRedirects: 0 });
  expect(redirect.status()).toBe(307);
  expect(redirect.headers()["content-security-policy"]).toBeUndefined();
});

test("a route handler whose path looks like an Open Graph image still goes through Clerk", async ({ request }) => {
  // Skipping Clerk there made auth() throw: a 500 instead of the sign-in redirect.
  for (const path of ["/api/exports/opengraph-image", "/api/invoices/twitter-image/document"]) {
    const response = await request.get(path, { maxRedirects: 0 });
    expect(response.status(), path).toBe(307);
  }
});
