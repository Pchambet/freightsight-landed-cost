"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * The scroll box around a table that holds nothing a keyboard can reach (no link, no button). When
 * the table is wider than its room — on a phone, almost always — or taller than a capped box, the box
 * takes the focus so the arrow keys can scroll it, and names what it holds (WCAG 2.1.1; axe
 * scrollable-region-focusable). When everything fits, it is a plain box and adds no tab stop. It is
 * positioned: a visually hidden caption or header (absolute) is then clipped by the box instead of
 * widening the page on a phone.
 */
export default function ScrollRegion({ label, className, children }: { label: string; className?: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [overflows, setOverflows] = useState(false);
  useEffect(() => {
    const box = ref.current;
    if (!box) return;
    const measure = () => setOverflows(box.scrollWidth > box.clientWidth + 1 || box.scrollHeight > box.clientHeight + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(box);
    if (box.firstElementChild) observer.observe(box.firstElementChild);
    return () => observer.disconnect();
  }, []);
  return (
    <div
      ref={ref}
      className={`relative overflow-x-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 ${className ?? ""}`}
      {...(overflows ? { tabIndex: 0, role: "region", "aria-label": label } : {})}
    >
      {children}
    </div>
  );
}
