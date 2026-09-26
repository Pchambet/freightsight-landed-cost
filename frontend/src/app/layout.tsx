import { ClerkProvider } from "@clerk/nextjs";
import { enUS, frFR } from "@clerk/localizations";
import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getTranslations } from "next-intl/server";
import { Toaster } from "sonner";
import { site } from "@/lib/site";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

// Every page reads live, per-user data: never prerender at build time.
export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("common");
  return {
    // Absolute base for every relative Open Graph/Twitter image and URL resolved below it
    // (A4-3: without this Next warns and falls back to localhost, so shared links break).
    metadataBase: new URL(site.url),
    title: { default: "FreightSight", template: "%s · FreightSight" },
    description: t("appDescription"),
  };
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const locale = await getLocale();
  return (
    <html lang={locale} className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col bg-slate-50 text-slate-900">
        {/* No messages at the root: the public site, the sign-in pages and a shared report render their
            words on the server, and shipping the whole dictionary (about 120 kB) to an anonymous visitor
            would only make those pages heavier. The application's own layout provides the messages its
            client components use; a client component outside it brings its own (see ProductShowcase). */}
        <NextIntlClientProvider messages={null}>
          <ClerkProvider localization={locale === "fr" ? frFR : enUS} taskUrls={{ "choose-organization": "/choose-organization" }} afterSignOutUrl="/">
            {children}
            <Toaster position="top-right" richColors />
          </ClerkProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
