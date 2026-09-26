import { defineConfig, devices } from "@playwright/test";

/**
 * Smoke tests for the public pages: no Clerk session, no API. The keys below are syntactically valid
 * placeholders (the publishable key encodes "clerk.example.com"). They must be `pk_live_` / `sk_live_`:
 * with a `pk_test_` key Clerk treats the instance as a development one and redirects every browser that
 * lacks its dev-browser cookie to the Frontend API for a handshake, which fails on the placeholder host
 * with ERR_NAME_NOT_RESOLVED. The marketing pages never call Clerk's servers, and every external request
 * is blocked in the tests, so the run is hermetic and offline.
 * The authenticated demo path needs real test keys — see e2e/README.md.
 */
const PORT = Number(process.env.E2E_PORT ?? 3100);
const baseURL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: { baseURL, trace: "on-first-retry" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npx next start -p ${PORT}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      // No backend is started: the public pages must not need one.
      API_URL: "http://127.0.0.1:3199",
      NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_live_Y2xlcmsuZXhhbXBsZS5jb20k",
      CLERK_SECRET_KEY: "sk_live_placeholder_for_ci_build_only",
      NEXT_PUBLIC_CLERK_SIGN_IN_URL: "/sign-in",
      NEXT_PUBLIC_CLERK_SIGN_UP_URL: "/sign-up",
    },
  },
});
