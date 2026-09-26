# Check-list de démo et de recette humaine

Ce fichier = **recette technique Odoo** (sync, push, brouillon).

Ce qui suit est **la partie que seule une personne peut vérifier** : le parcours dans le navigateur, avec
une vraie session et un vrai Odoo. Tout le reste (bloquants, TVA exclue, idempotence, isolation entre
organisations, rôle VIEWER refusé, erreur Odoo rendue en 502, clé API jamais réaffichée, sync
incrémentale, commandes annulées) est **couvert par les tests automatisés** et tourne à chaque commit ;
inutile de le refaire à la main avant chaque démo.

## Environnement

```bash
docker compose up -d --build                      # API :8001 + worker (factures, alertes)
# Odoo jetable : docs/odoo-questions.md §5 — base `fsdemo`, créée sans données de démo, en euros
python3 backend/scripts/odoo_seed.py --url http://localhost:8169 --db fsdemo
cd frontend && npm run dev                        # :3000
```

Connexion ERP dans Paramètres : adresse `http://host.docker.internal:8169` (l'API tourne dans Docker),
base `fsdemo`, identifiant `admin`, clé API `admin` (défauts de l'instance jetable, jamais ailleurs).
La base `fstest` des fixtures tient ses comptes en USD : une organisation en EUR s'y fait refuser
l'écriture (`erp_currency_mismatch`), ce qui est voulu mais gâche la scène.

**Organisation** : utilisez une organisation Clerk dédiée à cette recette (pas l'org « démo commerciale »
avec le jeu d'exemple pneus) — `odoo_seed.py` ne contrôle que le produit et le fournisseur qu'il crée
lui-même ; si votre instance Odoo jetable a aussi la démo mobilier standard d'Odoo (vendeurs « Wood
Corner », « Gemini Furniture »…), synchroniser dessus mélangerait deux histoires dans les Commandes.
Réinstaller Odoo sans les données de démo (`--without-demo=all` à la création de la base) l'évite.

**Avant de rejoindre l'appel**, vérifiez que tout tourne (c'est ici, pas en scène 4 en direct, qu'on
découvre un service arrêté) :

```bash
docker compose ps                 # api, db, worker: Up
curl -sf http://localhost:8001/healthz && echo OK
curl -sf http://localhost:8169/web/login > /dev/null && echo "Odoo OK"
```

## A · Parcours de démo, à connaître par cœur (≈ 15 min)


| #   | Geste                                                             | Attendu                                                                                             |
| --- | ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| A1  | Paramètres → Connexion ERP → « Tester et connecter »              | Version Odoo et société affichées                                                                   |
| A2  | « Synchroniser maintenant »                                       | Commandes importées, dont P00012                                                                    |
| A3  | Nouveau conteneur, charger P00012 / TYR-20555R16-91V × 100        | FOB 1 400,00 €                                                                                       |
| A4  | Ajouter un coût **réel** Fret maritime 180,00 €                   | Ventilé > 0, entrée « Coût créé » dans l'historique                                                 |
| A5  | Carte « Écriture dans l'ERP », sans cliquer                       | Réception WH/IN/00007, total 180,00 €, bouton actif                                                 |
| A6  | Ajouter un second coût réel (camionnage 130,00 €)                 | La carte se met à jour seule : 2 coûts, 310,00 € (coût rendu 17,10 €/pneu, soit +22 % du FOB)       |
| A7  | « Créer le brouillon dans Odoo »                                  | Toast avec le nom du brouillon, bannière « Déjà poussé », entrée « Écriture ERP créée (brouillon) » |
| A8  | Odoo → Inventaire → Opérations → Ajustements → Coûts de réception | Brouillon « FreightSight MSCU… », deux lignes, **non validé**                                       |
| A9  | Recliquer le bouton                                               | Grisé ; un second push par l'API renverrait le même document                                        |




## B · Garde-fous à montrer si on vous les demande (≈ 10 min)


| #   | Provoquer                              | Attendu                                                              |
| --- | -------------------------------------- | -------------------------------------------------------------------- |
| B1  | Conteneur chargé, aucun coût           | « Aucun coût facturé n'est ventilé sur ce conteneur… », bouton grisé |
| B2  | Un coût en statut **Estimé** seulement | Même message : les estimations ne partent jamais                     |
| B3  | Fret réel + TVA import réelle          | L'aperçu ne montre que le fret                                       |
| B4  | Sélecteur de langue → English          | Les mêmes phrases, complètes, en anglais                             |
| B5  | Paramètres → Déconnecter               | La carte disparaît de la fiche conteneur                             |




## Phrase à poser dès l'appel de découverte avec un prospect Odoo

« Vos catégories de produits sont-elles en valorisation automatisée, FIFO ou coût moyen ? » Si non, la
landed cost est impossible dans Odoo et l'aperçu le dira avant d'écrire.

## Ce qu'on peut affirmer, et ce qu'on ne promet pas

Oui : ventilation auditable par ligne de commande ; lecture des commandes Odoo ; coût de réception créé en
brouillon, jamais validé par nous ; aperçu exact avant écriture ; TVA import récupérable exclue ; un
double clic ne crée pas deux documents ; isolation par organisation avec RLS.

Pas encore : autre ERP qu'Odoo 17 ; livraisons partielles réparties automatiquement ; produit et journal
Odoo configurables ; extraction de factures sans relecture ; tracking en temps réel sur tous les trades.