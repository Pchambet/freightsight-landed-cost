import { NextIntlClientProvider } from "next-intl";
import { AppShell } from "@/components/layout/AppShell";
import OrgGuard from "@/components/layout/OrgGuard";
import { api } from "@/lib/api/client";
import { requireAuth } from "@/lib/auth";

async function org(): Promise<{ name?: string; unreadAlerts: number; hasSampleData: boolean }> {
  try {
    const { data } = await api.GET("/api/v1/organization");
    return { name: data?.name, unreadAlerts: data?.unread_alerts ?? 0, hasSampleData: data?.has_sample_data ?? false };
  } catch {
    return { unreadAlerts: 0, hasSampleData: false };
  }
}

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const { orgId } = await requireAuth();
  const { name, unreadAlerts, hasSampleData } = await org();
  // The application's client components translate on the client: they get the messages here, and
  // only here (the root layout sends none to the public pages).
  return (
    <NextIntlClientProvider>
      <AppShell orgName={name} unreadAlerts={unreadAlerts} hasSampleData={hasSampleData}>
        {orgId ? <OrgGuard renderedFor={orgId} /> : null}
        {children}
      </AppShell>
    </NextIntlClientProvider>
  );
}
