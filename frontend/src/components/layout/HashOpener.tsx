"use client";

import { usePathname } from "next/navigation";
import { useEffect } from "react";

/**
 * A link to `/settings#erp` or `/container/…#suivi` points at a collapsible section: arriving on a
 * closed one is arriving nowhere. The section named by the hash — or the one that contains it — is
 * opened, then scrolled to, on load, on navigation and whenever the hash changes.
 */
export default function HashOpener() {
  const pathname = usePathname();
  useEffect(() => {
    const open = () => {
      const id = decodeURIComponent(window.location.hash.slice(1));
      if (!id) return;
      const target = document.getElementById(id);
      const section = target?.closest("details");
      if (!target || !section || section.open) return;
      section.open = true;
      target.scrollIntoView({ block: "start" });
    };
    open();
    window.addEventListener("hashchange", open);
    return () => window.removeEventListener("hashchange", open);
  }, [pathname]);
  return null;
}
