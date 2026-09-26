import * as Sentry from "@sentry/nextjs";
import { scrubShareTokens } from "@/lib/scrubShareTokens";

// Server-side error reporting. Off when no DSN is configured (local dev, CI).
Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN || undefined,
  environment: process.env.NEXT_PUBLIC_APP_ENV ?? "local",
  release: process.env.VERCEL_GIT_COMMIT_SHA,
  tracesSampleRate: 0.1,
  sendDefaultPii: false,
  enabled: Boolean(process.env.NEXT_PUBLIC_SENTRY_DSN),
  // The token of a shared report is in its page URL and must never reach Sentry.
  beforeSend: (event) => scrubShareTokens(event),
  beforeSendTransaction: (event) => scrubShareTokens(event),
});
