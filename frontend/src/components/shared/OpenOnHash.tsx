"use client";

import { useEffect } from "react";

/**
 * A link that names a folded section — « /container/…#journal », where the fix of a preparation item
 * is made — lands on it open, instead of on a closed heading further down. Rendered by the page that
 * holds the sections, so it runs once they are on screen, and again when a link on the page changes
 * the anchor.
 */
export default function OpenOnHash() {
  useEffect(() => {
    const open = () => {
      const id = decodeURIComponent(window.location.hash.slice(1));
      const target = id ? document.getElementById(id) : null;
      if (!(target instanceof HTMLDetailsElement)) return;
      target.open = true;
      target.scrollIntoView({ block: "start" });
    };
    open();
    window.addEventListener("hashchange", open);
    return () => window.removeEventListener("hashchange", open);
  }, []);
  return null;
}
