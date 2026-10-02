# FreightSight web app

Next.js 16 (App Router, React Server Components) front end for the FreightSight API. The browser never
calls the API directly: pages and server actions call it on the server with the user's Clerk session token,
through a typed client generated from the backend's OpenAPI schema.

## Layout

```
src/
├── app/
│   ├── (marketing)/     public pages: landing, /securite, /design-partner
│   ├── (app)/           signed-in application: containers, purchase orders, imports, invoices,
│   │                    reports, SKUs, alerts, audit log, periods, settings
│   ├── r/[token]/       read-only shared report links (noindex)
│   └── sign-in, sign-up, choose-organization
├── features/            one folder per domain: data loading, server actions, components
├── components/          shared UI, layout and chart components
├── lib/api/             openapi-fetch client + `schema.d.ts` generated from ../backend/openapi.json
├── i18n/                next-intl config; strings in ../messages/ (French default, English)
└── proxy.ts             Clerk middleware: optimistic redirects only; every page calls auth.protect()
```

## Run

```bash
npm install
# .env.local: API_URL and the Clerk keys, see "Getting started" in the root README
npm run dev                  # http://localhost:3000
```

Signed-in pages need a Clerk development instance with Organizations enabled (free tier); the
[root README](../README.md) lists the variables.

## Checks

```bash
npm run lint && npm run typecheck && npm run build
npm run api:types            # after any backend API change: regenerates src/lib/api/schema.d.ts
npm run e2e                  # Playwright smoke tests of the public pages, hermetic (see e2e/README.md)
```
