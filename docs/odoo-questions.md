# Connecteur Odoo — ce que j'ai vu sur un vrai Odoo, et ce qui reste ouvert

> **In English, in short.** This is the engineering log of the Odoo 17 connector, kept in French and
> dated as it was written. Everything below was exercised against a real Odoo 17 instance (local
> `odoo:17` + `postgres:16`, `purchase` and `stock_landed_costs` modules); the test fixtures are
> responses captured from it, not invented. What the real instance taught: a purchase line's quantity is
> in the line's unit of measure (20 *dozens* of a product weighed per piece), so the adapter converts it
> before any weight-based allocation; `hs_code` does not exist on `product.product` in Community, so the
> adapter asks `fields_get` first; a wrong password returns `False` instead of raising; and a landed
> cost's allocation lines cannot be set at creation, only overwritten after Odoo computes its own
> (section 6). What is not verified: Odoo API keys instead of a password, Odoo Online rate limits, large
> databases, and the Odoo screen itself (everything was checked by reading records back over XML-RPC).
> The connector never validates a landed cost: posting entries in someone's books is their decision.

**Vérifié pour de vrai**, pas seulement sur des fixtures : `odoo:17` + `postgres:16` en local,
base `fstest` initialisée avec le module `purchase` et ses données de démonstration, authentification
XML-RPC et lecture réelles (`17.0-20260908`, 10 commandes, uid 2). Les fixtures de test
(`backend/tests/fixtures/odoo/purchase_orders.json`) sont **des réponses capturées de cette instance**,
pas des inventions.

## 1. Trois choses que le vrai Odoo m'a apprises

**La quantité d'une ligne est dans l'unité de la ligne, pas dans celle du produit.** Les données de
démonstration commandent « 20 Dozens » d'un produit dont le poids est au *pièce*. Importer 20 là où
le client veut dire 240 sous-estimerait toutes les ventilations au poids — c'est-à-dire une erreur
silencieuse sur le coût de revient. L'adapter convertit avec le rapport d'UdM et divise le prix
unitaire d'autant.

Reste un résidu assumé : 500 par douzaine devient 41,6667 par pièce (la précision de notre colonne),
soit un total de 10 000,008 au lieu de 10 000. Huit millièmes sur dix mille, visibles dans un test,
invisibles dans un coût de revient. → Si ça gêne un jour, il faudra stocker le prix à plus de quatre
décimales, ou garder l'UdM d'origine et convertir au moment de la ventilation.

**`hs_code` n'existe pas sur `product.product` en Odoo 17 Community.** Il arrive avec des modules de
localisation ou l'Enterprise. L'adapter demande à `fields_get` ce que l'instance possède et ne lit le
champ que s'il est là, au lieu d'échouer chez un client qui ne l'a pas. Les codes SH resteront donc
souvent vides à l'import ; c'est le barème douanier de l'organisation qui prendra le relais.

**Un mauvais mot de passe n'est pas une exception** : `authenticate` renvoie `False`. Un appelant qui
ne teste pas se retrouve avec `uid = False` et un échec incompréhensible trois appels plus loin. Un
mauvais *champ*, lui, lève un `Fault` contenant une trace Python complète, dont seule la dernière
ligne est montrable à un humain — c'est ce que fait `_fault_reason`.

## 2. Ce que je n'ai pas pu vérifier

- **Une clé API Odoo** (Préférences → Sécurité du compte → Clés API) plutôt que le mot de passe
  administrateur : le principe est le même côté XML-RPC (la clé se passe à la place du mot de passe),
  mais je n'en ai pas créé une sur l'instance de test. → À confirmer chez le premier client.
- **Odoo Online (odoo.com)** : XML-RPC y est ouvert, mais avec des limites de débit dont je n'ai pas
  la valeur. → À surveiller au premier client hébergé chez eux.
- **Les grosses bases.** Les commandes sont maintenant lues par pages de 200 avec un **curseur sur
  l'id** (jamais un offset : le personnel du client continue de confirmer des commandes pendant la
  synchronisation, et un offset sauterait ce qui s'insère entre deux pages). Les lignes, produits et
  UdM d'une même page restent lus en un appel chacun. La synchronisation est incrémentale (filtre
  `write_date >=` depuis la dernière synchronisation réussie, avec un recouvrement ; `full=true` relit
  tout). → Le comportement sur une base de plusieurs milliers de commandes n'est pas mesuré.

## 3. Choix de conception

- **L'import passe par le pipeline existant.** Les lignes Odoo sont écrites en CSV dans nos colonnes
  canoniques et données au même code qu'un tableur déposé à la main : mêmes clés naturelles, même
  idempotence, même rapport ligne à ligne, même `ImportJob` consultable. Un second chemin d'écriture
  serait un second endroit où les règles dérivent.
- **Le test de connexion précède l'enregistrement.** Une connexion enregistrée qui n'a jamais réussi
  à s'authentifier est une promesse que la prochaine synchronisation va rompre, et l'erreur d'Odoo
  est bien plus utile pendant que la personne a encore les identifiants sous les yeux.
- **La clé API est scellée** (AES-GCM, `ERP_ENCRYPTION_KEY`, magie `FSERP1`) et n'est jamais renvoyée
  par l'API, sous aucune forme. C'est un identifiant vers la comptabilité d'un client : la chose la
  plus sensible que cette application détiendra.
- **Une seule connexion par organisation** pour l'instant. Un groupe avec deux sociétés Odoo devra
  attendre ; la contrainte est explicite plutôt qu'implicite.
- **Rien n'est écrit vers Odoo.** La `stock.landed.cost` est une autre brique, et écrire dans la
  compta d'un client a un prix de l'échec bien plus élevé que lire.

## 4. Sur la synchronisation

- `POST /erp/sync` est **synchrone** : quelqu'un vient de connecter son Odoo et regarde le bouton. Le
  rafraîchissement quotidien (05:15 UTC) est le job.
- Une synchronisation qui échoue laisse une ligne `erp_sync_runs` qui dit pourquoi, et le message est
  aussi posé sur la connexion (`last_error`). Une synchronisation qui échoue en silence, c'est un
  client qui croit ses commandes à jour.
- Le job traite chaque organisation dans sa propre transaction : l'Odoo cassé d'un client n'empêche
  pas les autres de se synchroniser.
- **Une commande annulée est marquée, jamais supprimée** (`purchase_orders.status = CANCELLED`,
  `cancelled_at`) — décision provisoire, à confirmer avec un partenaire.
  Elle peut déjà porter des coûts et des conteneurs, et la supprimer réécrirait un coût de revient en
  silence. Une commande annulée que nous n'avons **jamais vue** n'est pas créée : elle n'a rien à
  nous dire. Le statut ne change rien à la ventilation : les chargements existants continuent de
  recevoir leur part, parce que la marchandise, elle, a bien voyagé.

## 5. Reproduire l'instance de test

```bash
docker network create odoo-test
docker run -d --name odoo-db --network odoo-test \
  -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=odoo -e POSTGRES_DB=postgres postgres:16
docker run -d --name odoo17 --network odoo-test -p 8169:8069 \
  -e HOST=odoo-db -e USER=odoo -e PASSWORD=odoo odoo:17
docker exec odoo17 odoo -d fstest -i purchase,stock_landed_costs --stop-after-init \
  --db_host odoo-db --db_user odoo --db_password odoo
# puis : http://localhost:8169, base fstest, admin / admin
python backend/scripts/odoo_seed.py --url http://localhost:8169 --db fstest

# base de démonstration, en euros : sans données de démo, sinon la devise ne peut plus changer
docker exec odoo17 odoo -d fsdemo -i purchase,stock_landed_costs --without-demo=all \
  --stop-after-init --db_host odoo-db --db_user odoo --db_password odoo
python backend/scripts/odoo_seed.py --url http://localhost:8169 --db fsdemo
```

Deux bases, deux usages. `fstest` porte les données de démonstration d'Odoo : c'est elle que citent
les identifiants des fixtures, et sa société tient ses comptes en **USD** — une organisation
FreightSight en EUR s'y voit refuser l'écriture (`erp_currency_mismatch`), ce qui est le
comportement voulu. `fsdemo` est vide à la création : le script y passe la société en **EUR** avant
toute écriture comptable (Odoo interdit le changement dès la première), puis crée la commande
`P00001` et sa réception `WH/IN/00001`. C'est celle qu'on branche pour la démo commerciale.
Recréer une base du même nom pendant que le serveur tourne lui laisse un registre périmé
(`column … does not exist`) : `docker restart odoo17` avant de lancer le script.

`stock_landed_costs` est nécessaire pour l'écriture retour ; `purchase` seul suffit pour la lecture.

`scripts/odoo_seed.py` amène l'instance à l'état exact d'où une landed cost est poussable, en
XML-RPC uniquement (aucun écran) : catégorie `fifo` + `real_time`, produit `TYR-20555R16` avec SKU,
commande confirmée, réception **validée**. Il est idempotent — deux exécutions donnent le même état
et le même résumé — et il imprime le numéro de commande, le SKU et la quantité à recréer côté
FreightSight. Sur une base `fstest` fraîche il redonne les identifiants des fixtures (produit 37,
commande `P00012`, réception `WH/IN/00007`, mouvement 40), ce qui rend les captures rejouables.

Deux pièges qu'il encapsule : la catégorie doit être écrite en `fifo` / `real_time` (tous les
produits de démonstration sont en `standard` / `manual_periodic`, et Odoo ne refuse qu'**à la
validation**, donc après notre écriture) ; et en 17 il faut poser `quantity` **et** `picked` sur
chaque mouvement avant `button_validate`, sinon Odoo répond un assistant « transfert immédiat » —
un dictionnaire là où on attendait un état.

Les tests n'en ont pas besoin : ils rejouent les fixtures capturées.


---

# Écriture retour : la landed cost (9 septembre 2026)

**Vérifié sur un vrai Odoo 17** avec le module `stock_landed_costs`, pas seulement sur des fixtures :
catégorie de produit en FIFO + valorisation automatisée, commande d'achat confirmée, réception
validée (WH/IN/00007), puis `stock.landed.cost` créée par notre code, relue, et vérifiée ligne à
ligne. Les fixtures de test sont les réponses de cette instance.

## 6. La réponse à la question ouverte : on ne peut pas poser les ventilations à la création

`valuation_adjustment_lines` **n'est pas exploitable en création** : Odoo les calcule lui-même (il en
a créé une à la création, puis l'a remplacée au `compute_landed_cost`). En revanche
`additional_landed_cost` sur chacune de ces lignes **est modifiable ensuite**. La séquence qui marche,
mesurée :

1. `create` de la `stock.landed.cost` avec `picking_ids`, `account_journal_id`, `target_model='picking'`
   et une `cost_lines` par coût réel (montant = ce que **notre** moteur a ventilé sur ce conteneur) ;
2. `compute_landed_cost` — Odoo génère ses lignes de ventilation avec **sa** répartition ;
3. `write` de `additional_landed_cost` sur chaque ligne avec **notre** montant exact ;
4. on s'arrête là : l'objet reste en `draft`.

Vérifié en vrai sur deux coûts : `LC` en brouillon à 3 041,67 avec deux lignes de ventilation portant
2 766,67 et 275,00, chacune rattachée au bon `stock.move` et à la bonne `cost_line`.

**L'appariement se fait sur l'`id` de la ligne de coût, pas sur son libellé** : Odoo tronque le nom
qu'il stocke et un utilisateur peut le renommer, donc apparier sur le texte écrirait silencieusement
zéro ligne. C'est le genre de bug qui ne se voit qu'en production, sur les livres de quelqu'un
d'autre.

Conséquence de conception : **une `cost_lines` par coût réel**, avec pour montant la somme de nos
ventilations sur ce conteneur — ainsi `amount_total` de la landed cost égale la somme des
ventilations, et le comptable ne voit pas un total qui ne correspond pas à ses lignes.

## 7. Ce qui bloque, et qu'on refuse avant d'écrire

- **Produit non valorisé en coût réel.** Odoo refuse la landed cost à la validation si la catégorie
  n'est pas en valorisation automatisée avec FIFO ou coût moyen — c'est-à-dire **après** que nous
  aurions écrit. On lit `valuation` et `cost_method` sur le produit et on refuse avant, avec la
  raison. (Les données de démo d'Odoo sont toutes en `standard` / `manual_periodic` : le cas par
  défaut est le cas bloquant.)
- **Aucune réception faite** pour la commande : une landed cost s'accroche à une réception.
- **Plusieurs réceptions** pour les commandes du conteneur (livraison partielle) : on ne devine pas
  laquelle, on le dit. → À trancher avec un partenaire : répartir au prorata des quantités reçues ?
- **Ligne sans correspondance** : l'appariement se fait sur le SKU (`default_code`), seul
  identifiant que les deux systèmes partagent. Une ligne sans SKU des deux côtés n'est pas poussée.
- **Coûts déjà poussés** : un second appel renvoie le premier `erp_pushes`, il n'écrit pas un
  deuxième document dans les livres de quelqu'un.

## 8. La TVA import ne part jamais dans Odoo (10 septembre 2026)

`_allocations` ne filtrait que sur `status is ACTUAL`. Une TVA import facturée — allouée pour la vue
trésorerie mais **récupérable**, donc exclue du coût de revient partout ailleurs — serait donc partie
dans la landed cost et aurait gonflé la valorisation de stock du client d'une taxe qu'il récupère.
Corrigé en réutilisant `engine.EXCLUDED_FROM_LANDED`, la liste du moteur lui-même : une seconde liste
ici aurait dérivé de la première au premier type de coût ajouté. Test : conteneur avec fret + TVA
import → l'aperçu ne montre que le fret, et le total aussi.

## 9. Les bloquants sont traduisibles

Chaque bloquant porte maintenant `params`, les valeurs dynamiques de sa phrase, à côté du `message`
anglais qui ne bouge pas (les clients API le lisent). Un écran écrit donc sa propre phrase dans sa
propre langue au lieu d'afficher la nôtre en dessous d'un titre traduit.

| code | params |
|---|---|
| `no_erp_connection` | — |
| `no_actual_costs` | — |
| `no_receipt` | `po_numbers` |
| `multiple_receipts` | `count`, `receipts` |
| `not_valued` | `products` |
| `unmatched_line` | `po_number`, `line_no`, `sku`, `receipt` |
| `already_pushed` | `existing` (liste jointe par « , » depuis le push différentiel) |

`partially_pushed`, introduit le 12 septembre pour refuser le double comptage, **a été retiré** le
jour même : le push différentiel le rend sans objet. Voir section 11.

Deux conventions : les listes sont **déjà jointes par « , »** (une chaîne, pas un tableau), et un SKU
absent vaut `""` — c'est à l'écran de décider si ça se dit « sans SKU » ou « SKU manquant », pas à
nous de lui faire analyser notre anglais.

**`multiple_receipts` est un code nouveau.** Il partageait `no_receipt` avec « aucune réception
trouvée », et aucun écran ne peut écrire deux phrases différentes à partir d'une seule clé de
traduction : « aucune réception pour P00012 » et « 2 réceptions correspondent » ne sont pas la même
information.

## 10. Un push interrompu se reprend, il ne se répète pas (12 septembre 2026)

Créer le brouillon chez le client et l'enregistrer chez nous sont **deux écritures dans deux
systèmes**. Entre les deux, il y a une fenêtre : `create_landed_cost` part d'abord, l'`ErpPush` est
ajouté ensuite, et le commit arrive encore après, dans la couche API. Si ce commit échoue — connexion
perdue, contrainte, redémarrage — le brouillon existe dans les livres du client sans aucune trace
chez nous, et le geste évident (recliquer) en crée un second.

**La clé n'est pas le conteneur, et ce n'est pas le nom du document.** Sur l'instance de test, trois
brouillons s'appellent aujourd'hui `FreightSight MSCU4821990` : le même conteneur est légitimement
repoussé quand une facture arrive plus tard, donc le nom ne distingue rien. Ce qui doit correspondre,
c'est **l'ensemble des coûts** : `marker_for()` trie les ids de coûts, les hache, et garde 16
caractères hexadécimaux — assez court pour tenir dans une note qu'un humain peut lire, et ne sortant
aucun identifiant de notre base vers celle de quelqu'un d'autre.

**Où il est écrit : `description`.** Vérifié par `fields_get` sur l'instance réelle : c'est un champ
`text`, stocké, modifiable, sans aucun sens comptable (« Item Description » dans le formulaire). Le
`name`, lui, est `readonly` et c'est la référence que lit le comptable — y coller une empreinte
serait du bruit dans ses livres. La note écrite dit pourquoi elle est là, pour qui ouvre le
brouillon.

La recherche est bornée à `state = draft` **et** à la même réception : une landed cost validée est
passée en comptabilité et n'est plus à nous, et un brouillon accroché à une autre réception n'est pas
le document qu'on fabriquait.

**Deux refus plutôt que deux devinettes :**
- Plusieurs brouillons portant la même empreinte → `ERP_DUPLICATE_DRAFT`. En choisir un laisserait
  l'autre traîner dans les comptes.
- Un brouillon dont le nombre de lignes de coût ne correspond plus au nôtre → `ERP_DRAFT_EDITED`.
  Quelqu'un l'a modifié depuis ; réécrire nos montants par-dessus serait décider à sa place ce qu'il
  voulait changer dans ses propres livres.

**Vérifié en vrai** sur l'Odoo 17 du port 8169, pas seulement sur le fake : premier push → brouillon
3 041,67 ; second push avec la même empreinte → **même id**, `adopted=True`, un seul document porte
l'empreinte, et les ventilations valent toujours 2 766,67 et 275,00. Un ensemble de coûts différent
n'adopte pas ce brouillon. (`compute_landed_cost` recrée les lignes de ventilation avec de nouveaux
ids à chaque appel — c'est pour ça qu'on réapplique notre split après, à l'adoption comme à la
création.)

## 11. Le push différentiel (12 septembre 2026)

**Le problème.** `plan()` recalculait sur *tous* les coûts réels du conteneur, et seul un ensemble
**exactement** identique était bloqué. Fret poussé, puis facture de douane, second push → un
deuxième document portant fret **+** douane. Une landed cost **s'ajoute** à la valorisation du
stock, elle ne la remplace pas : le fret était imputé deux fois à la marchandise, dans les livres de
quelqu'un d'autre, là où ce n'est pas nous qui l'aurions vu. Bloqué d'abord (`partially_pushed`),
puis tranché : on pousse la différence.

**Ce que ça donne.** Chaque push ne porte que les coûts qu'aucun `erp_pushes` vivant ne porte
encore. L'aperçu montre **tous** les coûts, chacun avec `pushed_as` (le document qui le porte déjà,
ou `null`) et `pushed_push_id` ; `total` ne compte que ce qui partira ; `pushable` = il reste au
moins un coût non poussé et rien ne bloque. Plus rien à pousser → `already_pushed`, avec la liste
des documents jointe par « , », jamais `no_actual_costs` qui serait faux.

**`pushed_push_id` en plus de `pushed_as`** : le nom ne désigne rien. Trois brouillons de
l'instance de test s'appellent `FreightSight MSCU4821990`. Conséquence directe : le nom du document
porte maintenant les **numéros de facture** qu'il transporte (`FreightSight MSCU4821990 —
FA-2026-1104`), sans quoi le `already_pushed` d'un conteneur à deux documents serait « poussé comme
X, X ».

**Un bloquant ne regarde que ce qui part.** `unmatched_line` ne se déclenche plus pour un coût déjà
poussé : c'est de l'histoire, et savoir si sa ligne correspond encore aujourd'hui ne change rien.

**Un double-clic reste sans danger** : quand il ne reste rien à pousser, le POST renvoie le dernier
push existant (201) au lieu d'une erreur. C'est l'aperçu qui porte le bloquant `already_pushed`.

## 12. Oublier un push, et pourquoi ce n'est pas une suppression

`DELETE /containers/{id}/erp/pushes/{push_id}?reason=…` **marque** la ligne (`forgotten_at`,
`forgotten_reason`, `forgotten_by`, migration 0015) et écrit une entrée d'audit `erp.push_forgotten`.
La ligne ne part jamais : c'est la seule trace qu'on a un jour écrit dans la comptabilité de
quelqu'un, et le journal d'audit est en ajout seul. Ce qui change, c'est qu'elle cesse de **porter**
ses coûts, qui redeviennent poussables.

**On vérifie d'abord auprès d'Odoo.** Tant que le document existe, il porte ces coûts : oublier le
push laisserait écrire la même charge une seconde fois, c'est-à-dire exactement l'accident que tout
ce mécanisme existe pour empêcher. → `409 ERP_DOCUMENT_STILL_THERE`, avec le nom et l'état du
document dans la réponse. Sans connexion ERP on ne peut pas vérifier, donc on refuse aussi
(`ERP_NO_CONNECTION`) plutôt que de croire sur parole.

`GET /containers/{id}/erp/pushes` liste les documents d'un conteneur, **oubliés compris et marqués
comme tels** : les cacher ferait du journal une histoire plus courte que ce qui s'est passé.

**Vérifié sur le vrai Odoo**, et le scénario est rejouable : `tests/test_erp_landed_cost_live.py`
tourne contre l'instance réelle quand `ODOO_TEST_URL` est défini (ignoré sinon, donc gratuit en CI)
et refait toute la chaîne — fret poussé, douane ajoutée, second document portant **275,00 et rien
d'autre** avec sa ligne de ventilation à 275,00, troisième push → `already_pushed`, oubli refusé
pendant que le brouillon existe, brouillon supprimé en XML-RPC, oubli accepté, douane repoussable.
Il nettoie les documents qu'il a créés, quoi qu'il arrive.

**Ce qui reste ouvert.** Un conteneur à plusieurs réceptions est toujours refusé (`multiple_receipts`).
Le produit « landed cost » et le journal sont toujours choisis automatiquement (premier par id).

## 13. Deux défauts trouvés à la relecture (12 septembre 2026)

Deux défauts que les tests ne pouvaient pas voir parce que le faux Odoo n'avait qu'une ligne de
réception et que rien ne modifiait un coût après son push.

**Une ligne de réception à laquelle on n'a rien ventilé gardait la part d'Odoo.** `compute_landed_cost`
répartit chaque ligne de coût sur *toutes* les lignes de la réception ; on ne réécrivait que celles où
notre moteur avait un montant. Une réception à deux produits dont le conteneur n'en porte qu'un
laissait donc à l'autre la part « égale » d'Odoo, et la somme des ventilations dépassait la ligne de
coût. Odoo refuse alors de valider, avec un message qui ne nomme aucune ligne (« Cost and adjustments
lines do not match »). Corrigé : toute ventilation d'une de nos lignes de coût qui n'est pas dans notre
répartition est **écrite à zéro**, pas ignorée. Test avec une réception à deux mouvements.

**Un coût modifié ou supprimé après son push laissait le brouillon faux, en silence.** Le push marquait
le coût comme « porté » par son id, pour toujours ; si son montant, son taux, sa méthode ou ses
chargements changeaient ensuite, le brouillon dans Odoo gardait l'ancien chiffre et rien ne le
disait, ni ici ni là-bas. Corrigé : `erp_pushes.carried` (migration 0017) mémorise par coût le montant
et le libellé au moment de l'écriture, et l'aperçu compare. Deux bloquants : `pushed_cost_changed`
(params `existing`, `changes` = « libellé : ancien → nouveau ; … », `count`) et `pushed_cost_gone`
(params `existing`, `costs`, `count`). Le remède est le même et il est dit dans la phrase : supprimer
le brouillon dans Odoo, oublier le push ici, pousser à nouveau. Les pushes antérieurs à la migration
n'ont pas de montants mémorisés : seule leur existence est vérifiée.

Limite laissée telle quelle : `_moves_by_sku` garde le **premier** mouvement par SKU. Une réception qui
a deux mouvements pour le même produit (deux lignes de commande, ou un reliquat) reçoit toute notre
ventilation sur le premier. Odoo valorise alors ce mouvement seul. À traiter le jour où un partenaire
a ce cas ; il se voit dans l'aperçu (les quantités).

## 14. Quatre règles revues (17 septembre 2026)

Quatre règles ont bougé, toutes pour la même raison : le connecteur se grippait sur des cas banals.

- **Même SKU deux fois dans une réception.** L'appariement se fait d'abord par l'identifiant de la
  ligne de commande, que la ligne de réception porte déjà ; le SKU ne sert plus qu'à défaut, et
  seulement s'il ne désigne qu'un mouvement. Les montants qui tombent légitimement sur le même
  mouvement sont additionnés (ils étaient écrasés : Odoo recevait 333 € sur 1 000 € et refusait la
  validation sans nommer de ligne). Un SKU qui désigne plusieurs mouvements sans identifiant pour
  trancher donne le bloquant `ambiguous_line`. La somme des parts est vérifiée avant tout appel.
- **Oublier un push dont le document est validé ou annulé.** La section 12 exigeait que le document ait
  disparu d'Odoo. Or un coût de réception validé ne se supprime pas : il a passé des écritures. Un
  coût corrigé le lendemain de la validation verrouillait donc le conteneur pour toujours. On peut
  désormais oublier un push dont le document est `done` ou `cancel`, contre un motif écrit, avec
  l'entrée d'historique ; tant qu'il est en brouillon, le refus reste (on le supprime dans Odoo
  d'abord). La correction comptable passe par un nouveau document d'ajustement.
- **La dérive se dit, elle ne bloque plus le reste.** `pushed_cost_changed` et `pushed_cost_gone`
  restent affichés sur la fiche, avec l'ancien et le nouveau montant et la marche à suivre, mais ils
  n'empêchent plus de pousser un coût sans rapport (la facture de surestaries de la semaine
  suivante). Contrepartie assumée, relevée en relecture : tant que la personne ne refait pas le
  document, Odoo porte l'ancien chiffre et FreightSight le nouveau. La remarque reste à l'écran
  jusqu'à ce que ce soit fait. Si un partenaire s'y perd, la suite logique est une case
  « j'ai compris » exigée avant le push.
- **Devise de la société Odoo.** Si elle diffère de la devise de base de l'organisation, le push est
  refusé (`erp_currency_mismatch`) au lieu d'écrire des euros dans une comptabilité en dollars.

Les erreurs du connecteur arrivent maintenant à l'écran par code (`ERP_UNREACHABLE`,
`ERP_AUTH_FAILED`, `ERP_TIMEOUT`, `ERP_BAD_RESPONSE`), l'hôte affiché sans identifiants ni errno ;
le détail technique reste dans les logs. Et l'URL saisie par le client est contrôlée
(`app/core/net.py`) : pas d'adresse privée, de boucle locale, de lien local ni de nom en `.internal`
en production, ports 80, 443, 8069 et 8071 seulement. Limite connue : le nom est résolu une seconde
fois par `xmlrpc` au moment de l'appel ; un DNS qui change de réponse entre le contrôle et l'appel
passerait. Le transport n'offre pas de prise pour épingler l'adresse.

## 15. Ce que je n'ai pas vérifié

**L'écran Odoo n'a pas été regardé.** Tout ce qui est écrit ci-dessus vient de la lecture XML-RPC
des enregistrements réellement créés (`name`, `state`, `amount_total`, `cost_lines`,
`valuation_adjustment_lines`, `former_cost`, `additional_landed_cost`, `final_cost`). Pour le voir,
l'instance se recrée en quatre commandes (section 5), identifiants `admin` / `admin`.

**Pas de validation, jamais.** `button_validate` poste des écritures comptables ; c'est la décision
de celui dont ce sont les livres, dans son écran à lui.

**Le produit « landed cost »** est cherché automatiquement (`landed_cost_ok = True`, le premier par
id) et le journal aussi (premier journal `general`). Si le client en a plusieurs, on prend le premier,
ce qui est un choix par défaut discutable. → À rendre configurable sur la connexion ERP le jour où un
partenaire s'en plaint.

## 16. Durcissement des chemins d'erreur (12 septembre 2026)

### Le vrai danger n'était pas un mauvais chiffre, c'était un worker qui ne revient jamais

**`ServerProxy` n'a pas de délai d'attente**, et son transport par défaut attend indéfiniment.
Mesuré, pas supposé : un appel vers 198.51.100.7 (TEST-NET-2, qui *jette* les paquets au lieu de les
refuser — exactement ce que fait le pare-feu d'un client qui change) était toujours bloqué au bout de
12 secondes, sans rien pour l'arrêter. Le balayage quotidien prend un `queueing_lock`, donc **l'Odoo
muet d'un seul client aurait arrêté la synchronisation de tous les autres**, pas seulement la sienne.

Corrigé par un transport qui pose un timeout sur la connexion (`_TimeoutTransport` et sa variante
https, `ERP_TIMEOUT_SECONDS`, 60 s par défaut — généreux, parce qu'un vrai Odoo qui lit deux cents
commandes n'est pas rapide ; fini, parce que l'autre option est un worker qui ne revient pas).
Revérifié après coup : `ErpUnavailable` au bout du délai exact, avec un message lisible.

### « Voir les logs applicatifs » n'est pas une information

Le handler du worker faisait `db.rollback()` sur échec, ce qui jetait le run et les champs que le
service venait de remplir, puis réécrivait une ligne disant *« the sync failed; see the application
logs for the reason »*. Le client ne lit pas nos logs. La raison est maintenant lue sur l'exception
**avant** le rollback et réécrite telle quelle : « Odoo is unreachable at … : timed out », ou la
dernière ligne du traceback Odoo.

### Backoff

`erp_connections.consecutive_failures` + `retry_after` (migration 0016). 15 min, puis 30, puis 1 h…
plafonné à 24 h. Le plafond compte plus que la courbe — le balayage est déjà quotidien — parce que la
protection doit survivre au jour où quelqu'un rendra la planification plus fréquente. Une lecture qui
réussit remet tout à zéro.

**Le bouton n'est jamais retenu.** Le backoff protège la *planification* d'un aller-retour inutile ;
quelqu'un devant l'écran vient de demander et mérite la réponse, même si c'est le même échec.
`sync_erp(db, org_id=…)` et `POST /erp/sync` ignorent donc `retry_after`.

`consecutive_failures` et `retry_after` sont dans `ErpConnectionResponse`, pour que l'écran puisse
dire « injoignable depuis X, prochaine tentative Y ».

### Deux fuites d'identifiant trouvées en chemin

Le test « le message ne doit jamais contenir la clé » a échoué la première fois, et il avait raison :
le texte d'erreur d'un ERP n'est pas à nous de croire. Un traceback rendu avec les variables locales,
la page d'erreur d'un proxy, ou une URL contenant les identifiants, et le secret part dans une
colonne de notre base puis sur un écran. Tous les messages d'erreur du connecteur passent maintenant
par `_safe()` : la clé devient `***`, et `://user:pass@` devient `://user:***@`.

Les deux refus d'URL portent un **code** que l'écran traduit (`ERP_URL_HAS_CREDENTIALS`,
`ERP_URL_SCHEME`) via `PydanticCustomError`, au lieu du `value_error` générique et de la phrase
anglaise préfixée « Value error, … » : ces messages-là sont lus dans un formulaire, par quelqu'un
qui ne lit pas forcément l'anglais. Le message anglais reste, en repli.

Deuxième fuite, indépendante : `erp_connections.url` est stocké **en clair** et renvoyé par l'API,
contrairement à la clé qui est scellée. Coller `https://admin:secret@erp.example.com` dans le
formulaire suffisait donc à afficher le mot de passe. Refusé à la saisie plutôt que nettoyé en
silence : retirer la moitié de ce qu'ils ont tapé échouerait à l'authentification plus tard, pour une
raison qu'ils ne verraient pas.
