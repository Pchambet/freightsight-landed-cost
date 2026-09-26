import * as Sentry from "@sentry/nextjs";
import { scrubShareTokens } from "@/lib/scrubShareTokens";

// Browser-side error reporting. The DSN is public by design; nothing personal is sent (sendDefaultPii off,
// no session replay), and the SDK stays off without a DSN.
Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN || undefined,
  environment: process.env.NEXT_PUBLIC_APP_ENV ?? "local",
  release: process.env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA,
  tracesSampleRate: 0.1,
  sendDefaultPii: false,
  enabled: Boolean(process.env.NEXT_PUBLIC_SENTRY_DSN),
  // The token of a shared report is in its page URL and must never reach Sentry.
  beforeSend: (event) => scrubShareTokens(event),
  beforeSendTransaction: (event) => scrubShareTokens(event),
});

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
