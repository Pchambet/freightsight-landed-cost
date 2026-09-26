"use client";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { site } from "@/lib/site";

/**
 * "Se connecter" for a visitor, "Ouvrir l'application" for someone already signed in. Decided in the
 * browser: the public pages run without the Clerk middleware (see proxy.ts), so the server does not
 * know who is visiting — and must not, or every cookie-less visitor (a search engine, a link preview)
 * would first be sent through Clerk's handshake. The first paint is the visitor's link.
 */
export default function AccountLink({ className, onClick }: { className?: string; onClick?: () => void }) {
  const { isLoaded, isSignedIn } = useAuth();
  return isLoaded && isSignedIn ? (
    <Link href={site.appHome} onClick={onClick} className={className}>Ouvrir l&apos;application</Link>
  ) : (
    <Link href="/sign-in" onClick={onClick} className={className}>Se connecter</Link>
  );
}
