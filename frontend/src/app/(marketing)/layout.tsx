import Link from "next/link";
import { Ship } from "lucide-react";
import AccountLink from "@/features/marketing/AccountLink";
import MobileNav from "@/features/marketing/MobileNav";
import { site } from "@/lib/site";

const NAV = [
  { href: "/#produit", label: "Produit" },
  { href: "/#calculateur", label: "Calculateur D&D" },
  { href: "/design-partner", label: "Design partners" },
  { href: "/#tarifs", label: "Tarifs" },
  { href: "/securite", label: "Sécurité" },
];

export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div lang="fr" className="min-h-screen bg-white text-slate-900">
      <header className="relative border-b border-slate-200 bg-white/90 backdrop-blur sticky top-0 z-20 print:hidden">
        <div className="max-w-6xl mx-auto px-5 h-14 flex items-center gap-6">
          <Link href="/" className="flex items-center gap-2">
            <div className="bg-blue-600 p-1.5 rounded-md"><Ship className="w-4 h-4 text-white" /></div>
            <span className="text-lg font-semibold tracking-tight">Freight<span className="text-blue-600">Sight</span></span>
          </Link>
          <nav className="hidden md:flex items-center gap-1 text-sm">
            {NAV.map((n) => (
              <Link key={n.href} href={n.href} className="px-3 py-1.5 rounded-md text-slate-600 hover:bg-slate-100 hover:text-slate-900">{n.label}</Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2 text-sm">
            <AccountLink className="hidden md:inline-flex px-3 py-1.5 rounded-md text-slate-600 hover:bg-slate-100 hover:text-slate-900" />
            <a href={site.bookingUrl} className="inline-flex items-center whitespace-nowrap bg-slate-900 hover:bg-slate-800 text-white font-medium py-1.5 px-3.5 rounded-md">Réserver 20 minutes</a>
            <MobileNav items={NAV} />
          </div>
        </div>
      </header>
      {children}
      <footer className="border-t border-slate-200 mt-24 print:hidden">
        <div className="max-w-6xl mx-auto px-5 py-10 grid gap-8 md:grid-cols-3 text-sm text-slate-600">
          <div>
            <div className="font-semibold text-slate-900 mb-2">FreightSight</div>
            <p>Coût de revient rendu pour les PME importatrices. Pré-alpha, ouvert à cinq design partners.</p>
          </div>
          <div>
            <div className="font-semibold text-slate-900 mb-2">Données</div>
            <p>Hébergé à {site.hostingRegion}. Une base isolée par société, aucun modèle entraîné sur vos données. <Link href="/securite" className="text-blue-700 underline underline-offset-2 hover:no-underline">Page sécurité</Link>.</p>
          </div>
          <div>
            <div className="font-semibold text-slate-900 mb-2">Contact</div>
            <p><a href={site.bookingUrl} className="text-blue-700 hover:underline">Réserver 20 minutes</a> · <Link href="/design-partner" className="text-blue-700 hover:underline">Programme design partner</Link></p>
            <p className="mt-2"><a href={site.quickCheckUrl} className="text-blue-700 hover:underline">Envoyez une facture transitaire et le bon de commande</a> : coût rendu par référence sous 24&nbsp;h.</p>
            <p className="mt-3 text-xs text-slate-500">© 2026 FreightSight. Les montants affichés sur ce site sont des exemples.</p>
          </div>
        </div>
      </footer>
    </div>
  );
}
