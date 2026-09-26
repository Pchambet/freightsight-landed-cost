import "server-only";
import { z } from "zod";

// Server-only configuration. The browser never talks to the API: reads happen in Server Components,
// writes in Server Actions, both carrying the signed-in user's Clerk session token.
const schema = z.object({
  API_URL: z.string().url(),
  // Development only: when set, the X-Org-Id header is sent as well, so a local backend without
  // Clerk configuration (APP_ENV=dev) still resolves an organization. Never set in production.
  ORG_ID: z.string().uuid().optional(),
  NEXT_PUBLIC_APP_ENV: z.enum(["local", "preview", "prod"]).default("local"),
});

const parsed = schema.safeParse({
  API_URL: process.env.API_URL,
  ORG_ID: process.env.ORG_ID || undefined,
  NEXT_PUBLIC_APP_ENV: process.env.NEXT_PUBLIC_APP_ENV,
});

if (!parsed.success) {
  const issues = parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ");
  throw new Error(`Invalid environment. Copy .env.example to frontend/.env.local. ${issues}`);
}

export const env = parsed.data;
