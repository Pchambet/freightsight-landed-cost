"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** While the extraction job runs, re-render the server page every few seconds. */
export default function AutoRefresh({ everyMs = 3000 }: { everyMs?: number }) {
  const router = useRouter();
  useEffect(() => {
    const id = setInterval(() => router.refresh(), everyMs);
    return () => clearInterval(id);
  }, [router, everyMs]);
  return null;
}
