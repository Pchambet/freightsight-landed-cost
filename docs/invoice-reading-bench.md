# Lecture des factures de transitaire — ce qui est mesuré, et ce qui ne l'est pas

« Envoyez-nous une facture, on vous rend la ventilation » est l'offre d'entrée. Elle repose sur un
lecteur à règles (`backend/app/adapters/extraction/regex_extractor.py`) qui doit fonctionner sans clé,
sans réseau et sans envoyer le document à qui que ce soit. Jusqu'au 17 septembre 2026 sa qualité
n'était mesurée nulle part : un seul PDF d'essai dans le dépôt. Ce document décrit le banc qui la
mesure désormais, les chiffres, et leurs limites.

## Le banc

`backend/tests/bench_invoices_*.py` — 55 PDF construits comme de vrais tableaux (chaque cellule
posée à sa place, montants alignés à droite, plusieurs pages), pas des lignes de texte :

- **9 familles de mises en page** rencontrées sur des factures d'import françaises, chacune rendue
  pour 4 factures (libellés connus, libellés qu'aucune liste ne connaît, quantités > 1, fret en
  dollars converti sur la ligne) : quatre colonnes (désignation, quantité, PU, montant) ; code TVA
  après le montant (Sage, EBP) ; taxable / non taxable côte à côte ; devise convertie sur la ligne ;
  relevé d'armateur en anglais ; débours à part des prestations ; avoir ; plusieurs conteneurs sur
  une facture ; tableau sur deux pages avec report.
- **Chaque facture deux fois** : dessinée ligne par ligne, puis colonne par colonne — ce que font les
  logiciels qui construisent un tableau avec des cadres de texte. Le texte sort alors du PDF avec
  tous les libellés d'abord et tous les montants ensuite.
- **7 pièges**, écrits à part et avant de regarder si le lecteur s'en sortait : taux de TVA imprimé à
  droite du montant, libellé sur deux lignes, espace insécable et symbole €, montants avec centimes
  au-dessus du tableau (valeur en douane, poids, cours), ligne de TVA dans le tableau, TVA import en
  débours, totaux sans le mot « total » (« Montant HT », « Net à payer »).

Ce qui est compté, par facture : les lignes lues avec le bon montant, les lignes inventées, le type
de coût quand le libellé en désigne un, l'en-tête (numéro, date, devise, total HT, total, conteneurs),
et surtout l'**erreur silencieuse** : une lecture fausse que le contrôle arithmétique ne signale pas.
Une lecture a le droit d'être incomplète ; elle n'a pas le droit de l'être sans le dire.

Lancer le banc : `cd backend && PYTHONPATH=tests .venv/bin/python -m bench_invoices_scoring`.
`tests/test_invoice_bench.py` en fait un cliquet : ce qui est lu aujourd'hui doit l'être demain.

## Les chiffres

| | Avant (16 sept.) | Après (17 sept.) |
|---|---|---|
| Lignes lues — 9 familles, 48 factures | 82 / 278 (29 %) | 278 / 278 (100 %) |
| … dont tableaux dessinés colonne par colonne | 0 % | 100 % |
| … dont « code TVA après le montant » | 0 % | 100 % |
| Lignes lues — 7 pièges (à la première mesure, avant correction) | — | 19 / 24 (79 %), 7 inventées, **2 erreurs silencieuses** |
| Lignes lues — 7 pièges (après correction) | — | 24 / 24, 0 inventée, 0 silencieuse |
| Factures lues sans aucune erreur | 11 / 48 | 55 / 55 |
| Erreurs silencieuses | 0 | 0 |

Ce qui a changé dans le lecteur :

1. **Le texte est lu par sa géométrie** (`extraction_mode="layout"` de pypdf) : une ligne du tableau
   reste une ligne quel que soit l'ordre dans lequel le fichier dessine ses cellules, et l'écart entre
   deux colonnes reste visible — c'est aussi ce qui distingue « 2   285,00 » (deux manutentions à
   285 €) de « 2 285,00 ».
2. **Toute ligne du tableau est une ligne de frais**, que son libellé soit connu ou non. Avant, une
   ligne « Frais de sûreté portuaire », « Taxe d'escale », « Pesage VGM » ou « Remise commerciale »
   n'arrivait jamais à l'écran : le contrôle arithmétique disait que la facture ne tombait pas juste,
   et le relecteur devait trouver seul ce qui manquait. Elle arrive maintenant sans type de coût,
   confiance 0,50 (« à relire »), et c'est à une personne de la nommer.
3. Le tableau est repéré par sa ligne d'en-tête (ou, sans en-tête, par sa première ligne portant un
   montant avec centimes) et s'arrête à la première ligne de total. Sous-totaux, reports, valeur en
   douane, cours, poids et ligne de TVA de la facture ne sont pas des frais.
4. Le montant est la dernière colonne qui en porte un — sauf quand l'en-tête nomme la colonne
   « Montant » et que la ligne a une cellule dans chaque colonne : un taux de TVA « 20,00 » imprimé à
   droite n'est alors plus pris pour le montant.
5. Code TVA, devise ou symbole après le montant ; libellé sur deux lignes ; conteneur en titre de
   section ; numéro d'avoir ; TVA à l'importation en débours (`IMPORT_VAT`) ; espaces insécables,
   fines et étroites ; « 65,000,00 » (deux cellules collées) refusé plutôt que lu 65 000.
6. **Aucun total lisible → lecture non vérifiée** (`@unverified_total`, confiance plafonnée) : « ça
   tombe juste » ne peut pas se dire d'une lecture qui n'avait rien à quoi se comparer. C'était la
   cause des deux erreurs silencieuses des pièges.

## Ce que ces chiffres ne disent pas

- **Ce ne sont pas des factures de clients.** Ce sont les structures que j'en connais, et j'ai écrit
  le corpus et le lecteur : 100 % veut dire « ces familles sont couvertes », pas « 100 % des factures
  seront lues ». Les 7 pièges l'ont montré : écrits à l'aveugle, ils ont donné 79 % et deux erreurs
  silencieuses. Le prochain jeu écrit à l'aveugle donnera encore moins que 100 %.
- **Le vrai chiffre viendra de factures réelles.** Le banc sait les scorer : déposer `nom.pdf` et
  `nom.json` (numéro, date, devise, totaux, lignes attendues) dans
  `backend/tests/fixtures/invoices_real/` — dossier ignoré par git, rien ne quitte le poste. Dix
  factures de trois transitaires différents, obtenues du premier design partner, valent plus que tout
  ce corpus.
- **Un PDF scanné n'est pas lu du tout** (`NO_TEXT_LAYER`) : il n'a pas de couche texte, et le
  lecteur n'a pas d'OCR. C'est dit à l'écran, avec la saisie manuelle comme issue. À traiter ensuite :
  OCR local (Tesseract) avant les règles, mesuré par ce même banc sur des images des 55 PDF.
- **Limite connue de pypdf** : le texte « layout » garde l'ordre des colonnes, pas leur alignement
  (la même colonne finit à dix caractères d'écart d'une ligne à l'autre), et des cellules dessinées de
  droite à gauche dans une même ligne sortent collées. Le premier cas est contourné par le comptage
  des colonnes ; le second est refusé (montant illisible → ligne manquante → contrôle arithmétique).
- Le lecteur par modèle (`llm_extractor.py`, activé seulement si une clé est posée) n'est pas mesuré
  ici : le banc ne doit dépendre d'aucun service. Le même corpus peut le scorer à la demande.
