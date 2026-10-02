import Link from "next/link";
import { SearchX } from "lucide-react";

// Root 404: covers any URL that matches no route at all, marketing or app. The (app) group
// has its own not-found for a signed-in visitor inside the product; this one is what an anonymous
// visitor or a search crawler gets for a stale or mistyped link, so it stays in French and links
// back to the public site, not to /containers.
export const metadata = { title: "Page introuvable" };

export default function NotFound() {
  return (
    <main className="min-h-[70vh] flex items-center justify-center px-5 py-16">
      <div className="text-center max-w-md">
        <div className="w-12 h-12 bg-slate-100 text-slate-600 rounded-full flex items-center justify-center mx-auto mb-4">
          <SearchX className="w-6 h-6" aria-hidden />
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">Page introuvable</h1>
        <p className="mt-3 text-slate-600">Cette page n&apos;existe pas ou a été déplacée.</p>
        <Link
          href="/"
          className="mt-6 inline-flex items-center gap-2 bg-slate-900 hover:bg-slate-800 text-white text-sm font-medium py-2.5 px-5 rounded-md"
        >
          Retour à l&apos;accueil
        </Link>
      </div>
    </main>
  );
}
