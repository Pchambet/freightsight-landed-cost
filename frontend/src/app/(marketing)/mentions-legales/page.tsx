import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { prose } from "@/features/marketing/legalProse";
import { site } from "@/lib/site";

// Article 6-III of the LCEN. Rendered only once every field it requires is in site.publisher and
// site.hosts: until then the page is a 404 and nothing links to it (see lib/site.ts).
// While it is a 404, its browser tab says so like any other missing page.
export const metadata: Metadata = site.legalNoticePublished
  ? {
      title: "Mentions légales",
      description: "Éditeur, directeur de la publication et hébergeurs du site FreightSight.",
      alternates: { canonical: `${site.url}/mentions-legales` },
    }
  : { title: "Page introuvable" };

export default function LegalNoticePage() {
  if (!site.legalNoticePublished) notFound();
  const { publisher, hosts } = site;
  return (
    <main className={prose.main}>
      <div className={prose.eyebrow}>Mentions légales</div>
      <h1 className={prose.h1}>Qui publie ce site, et qui l&apos;héberge.</h1>

      <h2 className={prose.h2}>Éditeur</h2>
      <ul className="mt-3 space-y-1 text-slate-700">
        <li>{publisher.legalName}, {publisher.legalForm}</li>
        <li>SIREN {publisher.siren}{publisher.registry && <> ({publisher.registry})</>}</li>
        {publisher.vatNumber && <li>TVA intracommunautaire : {publisher.vatNumber}</li>}
        <li>{publisher.address}</li>
        <li>Téléphone : {publisher.phone}</li>
        <li>E-mail : <a href={`mailto:${publisher.email}`} className={prose.link}>{publisher.email}</a></li>
      </ul>

      <h2 className={prose.h2}>Directeur de la publication</h2>
      <p className={prose.p}>{publisher.publicationDirector}</p>

      <h2 className={prose.h2}>Hébergeurs</h2>
      {hosts.map((h) => (
        <div key={h.name} className="mt-4 text-slate-700">
          <div className="font-medium text-slate-900">{h.name}</div>
          <div className="text-sm text-slate-500">{h.role}</div>
          <div>{h.address}</div>
          <div>Téléphone : {h.phone}</div>
          <div><a href={h.url} className={prose.link}>{h.url.replace("https://", "")}</a></div>
        </div>
      ))}

      <h2 className={prose.h2}>Propriété intellectuelle</h2>
      <p className={prose.p}>Les textes, images, marques, logos et logiciels de ce site et du service FreightSight appartiennent à {publisher.legalName} ou sont utilisés avec l&apos;autorisation de leurs titulaires. Toute reproduction ou réutilisation sans autorisation écrite préalable est interdite.</p>

      <h2 className={prose.h2}>Données personnelles</h2>
      <p className={prose.p}>Le traitement des données personnelles est décrit dans la <Link href="/confidentialite" className={prose.link}>politique de confidentialité</Link>.</p>
    </main>
  );
}
