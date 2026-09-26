"use client";

import { useEffect, useRef, useState } from "react";
import { CircleHelp } from "lucide-react";

/**
 * Short glossary help: visible on hover and keyboard focus.
 *
 * The text is the button's name, so a screen reader says it once, on focus; the bubble is for the eye
 * only (hidden from assistive technology, or the text would be read a second time as a description
 * and a third time in the page's flow). The button is 24 px square (WCAG 2.2 target size) while
 * taking the room of the 18 px it looks like: the negative margin gives the difference back.
 *
 * Content that appears on hover or focus must stay while the pointer moves onto it, and go away on
 * Escape without moving the pointer or the focus (WCAG 1.4.13): the bubble takes the pointer, and
 * Escape — listened for on the document, since a hover puts no focus here — hides it until the next
 * hover or focus.
 */
export default function Hint({ text }: { text: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [dismissed, setDismissed] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const box = ref.current;
      if (e.key === "Escape" && box && (box.matches(":hover") || box.contains(document.activeElement))) setDismissed(true);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  return (
    <span ref={ref} className="relative inline-flex group/hint" onMouseEnter={() => setDismissed(false)} onFocus={() => setDismissed(false)}>
      <button
        type="button"
        aria-label={text}
        className="-m-[3px] p-[5px] rounded text-slate-500 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
      >
        <CircleHelp className="w-3.5 h-3.5" aria-hidden />
      </button>
      <span
        aria-hidden
        className={`absolute left-1/2 bottom-full z-20 w-[min(14rem,calc(100vw-2rem))] -translate-x-1/2 pb-1.5 transition-opacity ${
          dismissed ? "invisible opacity-0" : "invisible opacity-0 group-hover/hint:visible group-hover/hint:opacity-100 group-focus-within/hint:visible group-focus-within/hint:opacity-100"
        }`}
      >
        {/* The padding below the bubble is part of it, so the pointer crosses no gap on its way up. */}
        <span className="block rounded-md bg-slate-900 px-2.5 py-2 text-xs font-normal normal-case tracking-normal text-white shadow-lg">{text}</span>
      </span>
    </span>
  );
}
