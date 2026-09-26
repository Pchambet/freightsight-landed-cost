"use client";

import { useAuth } from "@clerk/nextjs";
import { useEffect } from "react";

const KEY = "fs.orgGuard";
/** Sent with every request this tab makes to its own server: the company this tab is looking at. */
export const TAB_ORG_HEADER = "x-fs-tab-org";

/**
 * The organisation the session cookie speaks for right now. Clerk's `__session` cookie is written by
 * its browser script (it is not HttpOnly) and is a JWT: `o.id` in the current claims format,
 * `org_id` in the previous one. Read only, never trusted: the server verifies the same token itself.
 */
function cookieOrg(): string | null {
  try {
    // A development instance writes both `__session` and `__session_<suffix>`, and on localhost the
    // bare one may belong to another application served from the same host: the suffixed cookie is
    // this application's own, so it wins when it exists.
    const all = document.cookie.split("; ");
    const raw = all.find((c) => /^__session_[A-Za-z0-9]+=/.test(c)) ?? all.find((c) => c.startsWith("__session="));
    const payload = raw?.slice(raw.indexOf("=") + 1).split(".")[1];
    if (!payload) return null;
    const claims = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/"))) as { o?: { id?: string }; org_id?: string };
    return claims.o?.id ?? claims.org_id ?? null;
  } catch {
    return null;
  }
}

/** A forced token refresh is a call to Clerk: at most one per disagreement, and never two within this many ms. */
const RECLAIM_EVERY_MS = 10_000;
/** Long enough for Clerk's own switch (token, cookie, router refresh) to finish by itself. */
const SETTLE_MS = 2000;

/**
 * The active organisation belongs to the Clerk session, not to the tab: switching company in one
 * tab can switch it under another. A page already on screen would then show one company while its
 * buttons act as another — the API refuses (the ids are not that company's), but "Introuvable" on a
 * record you are looking at is no way to find out. So a tab whose screen and session still disagree
 * after a moment goes back to the board, rendered for the company now active.
 *
 * `renderedFor` is the organisation the server rendered this layout for. The wait matters: during
 * an ordinary switch the two disagree for a few hundred milliseconds, and reloading in the middle
 * of it would race the cookie Clerk is about to write. One reload per change at most: if the server
 * still answers for the old organisation, reloading again would loop.
 */
export default function OrgGuard({ renderedFor }: { renderedFor: string }) {
  const { isLoaded, orgId, getToken } = useAuth();

  // The session cookie is one for all tabs, and the last tab to refresh its token wrote it: seen on
  // 17 Sept 2026 with two tabs on two companies, the server answered tab B for the company of tab A.
  // Coming back to this tab, ask Clerk for a fresh token of this tab's company, which is what makes
  // it write the cookie again, before the first click leaves in another company's name.
  useEffect(() => {
    if (!isLoaded) return;
    const claim = () => {
      if (document.visibilityState === "visible") void getToken({ skipCache: true }).catch(() => null);
    };
    window.addEventListener("focus", claim);
    document.addEventListener("visibilitychange", claim);
    return () => {
      window.removeEventListener("focus", claim);
      document.removeEventListener("visibilitychange", claim);
    };
  }, [isLoaded, getToken]);

  // The belt for the moment in between. A click in this tab becomes a request to our server — a
  // server action, a navigation — that the server reads in the name of whatever company the shared
  // cookie holds. So before any such request leaves: if the cookie speaks for another company than
  // this tab's, take the cookie back first. And say which company the tab is on, so that the server
  // can refuse an action whose cookie and tab still disagree (lib/auth.ts, `protectAction`).
  // Next's router calls the global `fetch` at request time, which is what makes this reach it.
  useEffect(() => {
    if (!isLoaded || !orgId) return;
    const original = window.fetch;
    // What was last reclaimed and when: a cookie this tab cannot win back (or cannot read right)
    // must not turn every request into a round trip to Clerk.
    let reclaimed: { held: string; at: number } | null = null;
    const guarded: typeof window.fetch = async (input, init) => {
      let next = init;
      try {
        const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url, window.location.href);
        if (url.origin === window.location.origin) {
          const held = cookieOrg();
          if (held && held !== orgId && (reclaimed?.held !== held || Date.now() - reclaimed.at > RECLAIM_EVERY_MS)) {
            reclaimed = { held, at: Date.now() };
            await getToken({ skipCache: true }).catch(() => null);
          }
          if (!(input instanceof Request)) {
            const headers = new Headers(init?.headers);
            headers.set(TAB_ORG_HEADER, orgId);
            next = { ...init, headers };
          }
        }
      } catch {
        // a request is never held back by its own safety belt
      }
      return original(input, next);
    };
    window.fetch = guarded;
    return () => {
      if (window.fetch === guarded) window.fetch = original;
    };
  }, [isLoaded, orgId, getToken]);

  useEffect(() => {
    if (!isLoaded || !orgId) return;
    if (orgId === renderedFor) {
      try {
        window.sessionStorage.removeItem(KEY);
      } catch {
        // nothing to forget
      }
      return;
    }
    const timer = window.setTimeout(() => {
      const change = `${renderedFor}>${orgId}`;
      try {
        if (window.sessionStorage.getItem(KEY) === change) return;
        window.sessionStorage.setItem(KEY, change);
      } catch {
        return; // no storage to remember the reload by: a stale page is better than a reload loop
      }
      // A full load on purpose: the layout and the router cache both belong to the other company.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign("/overview");
    }, SETTLE_MS);
    return () => window.clearTimeout(timer);
  }, [isLoaded, orgId, renderedFor]);
  return null;
}
