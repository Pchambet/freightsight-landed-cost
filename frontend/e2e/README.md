# End-to-end tests

`npx playwright test` (from `frontend/`) builds nothing: it starts `next start` on port 3100 through
Playwright's `webServer` and drives Chromium against it. Run `npm run build` first, and
`npx playwright install chromium` once.

## What is covered

The **public** pages only, without a Clerk session and without the API running:

- the landing page, `/securite` and the `/design-partner` one-pager render, with a booking link;
- the demurrage calculator computes, and reads its state from the query string;
- `/sign-in` renders and `/containers` redirects an anonymous visitor to it;
- `robots.txt` keeps the application out of search engines;
- a shared report link (`/r/<token>`) that does not exist is a real 404, `noindex`, with no sign-in redirect;
- the public pages (`/`, `/securite`, `/design-partner`, `/r/…`), their Open Graph images, `robots.txt` and `sitemap.xml` are served without the Clerk middleware, so a crawler that keeps no cookies is never sent to Clerk's handshake.

Every request that is not the local server is aborted in the tests, so a run is hermetic: no Clerk
network call, no FX call, nothing that can make CI flaky. The Clerk keys in `playwright.config.ts` are
the same syntactically valid placeholders CI already uses for `next build`.

One wrinkle makes that possible: a Clerk **development** instance answers the first document request of
a cookie-less browser with a 307 to its Frontend API — the dev-browser handshake — and an offline run
cannot follow it (`net::ERR_NAME_NOT_RESOLVED`, which is what you see if you drop the cookie below).
`test.beforeEach` seeds a `__clerk_db_jwt` cookie of any value, which is enough to skip the handshake;
the visitor stays anonymous, which is what these tests are about. `curl` never sees this because the
handshake only fires for requests that accept HTML.

## What is not covered, and what it would take

The interesting path — load the sample data, open a container, add a cost, watch the allocation change —
lives behind `auth.protect()`, so it needs a **real Clerk session**. Adding it means:

1. A Clerk **development instance** dedicated to CI, and its keys as GitHub secrets:
   `CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, plus a test user's
   `E2E_CLERK_USER_USERNAME` / `E2E_CLERK_USER_PASSWORD`. Only the repository owner can create these.
2. `@clerk/testing` as a dev dependency: `clerkSetup()` in a global setup (it mints the testing token
   that lets the sign-in run without a bot check) and `setupClerkTestingToken({ page })` in the specs.
3. The test user must belong to an organization, since every app page needs `orgId` — create it once in
   the Clerk dashboard, or through the Backend API in the global setup.
4. A backend for the run: the `postgres` service already used by the `backend` CI job, `alembic upgrade
   head`, `uvicorn` on 8001, and `API_URL` pointing at it. `POST /api/v1/organization/sample-data` then
   gives the run its dataset in one call, so no fixture SQL is needed.

Until those secrets exist, keep the authenticated path out of CI rather than mocking Clerk: a mocked
session would test the mock, not the guard.
