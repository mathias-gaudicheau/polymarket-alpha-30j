# Protocole — registre des stratégies déclarées

Ce fichier est le cœur scientifique de l'expérience. Une stratégie n'a le droit de recevoir
du capital qu'après avoir été inscrite ici, **avec ses règles complètes, avant** que la
moindre décision ne lui soit attribuée. Le commit fait foi : l'historique git est daté et
scellé, il ne peut pas être réécrit après avoir vu les résultats.

Sans cette contrainte, l'expérience n'aurait aucune valeur. On peut toujours trouver, après
coup, une règle qui aurait gagné sur les trente derniers jours. Ce qui se mesure ici est
autre chose : une règle écrite d'avance tient-elle sur des données qu'elle n'a jamais vues.

## Les quatre états

| État | Capital | Ce qu'il signifie |
|---|---|---|
| `candidat` | 0 $ | Repéré, pas encore formalisé. Aucune décision ne lui est attribuée. |
| `déclaré` | 0 $ | Règles écrites et scellées ici. Émet des signaux et les fait exécuter à blanc, sans capital. C'est le test hors échantillon. |
| `actif` | alloué | A satisfait les conditions de promotion ci-dessous. |
| `suspendu` | retiré | Sorti de ses bornes, ou coupe-circuit déclenché. |

## Conditions de promotion vers `actif`

Les quatre doivent être réunies. Elles sont vérifiées par script, jamais à l'œil.

1. **Au moins 30 paris dénoués** depuis la date de déclaration. Sous ce seuil, aucune
   conclusion n'est énoncée.
2. **Avantage supérieur aux frais**, mesuré et non annoncé.
3. **Borne basse de l'intervalle de confiance à 95 % au-dessus de zéro**, calculée par
   rééchantillonnage.
4. **Écart net au témoin aléatoire**, lui aussi significatif.

Une stratégie promue puis redescendue sous ces seuils repasse en `suspendu`. Son historique
antérieur reste au registre : rien n'est effacé.

## Comptage des observations indépendantes

Le nombre de paris n'est pas le nombre d'observations. Quinze positions sur une même échelle
de seuils bitcoin ne font qu'**une** observation : elles se dénouent ensemble. Le décompte
retenu est donc le nombre de **grappes indépendantes**, une grappe regroupant les positions
partageant le même sous-jacent et la même échéance.

Cette règle est écrite ici parce qu'elle est la façon la plus facile de se mentir à soi-même
en toute bonne foi : elle divise typiquement par cinq à dix la confiance qu'on croyait avoir.

---

## Stratégies déclarées

### c1_panier_sous_cote — couche 1 — déclarée le 2026-07-27

**Constat exploité.** Sur un événement à issues mutuellement exclusives et exhaustives,
exactement une se réalise. La somme des prix d'achat des issues doit donc valoir 1 $. Si
acheter le panier entier coûte moins, la différence est acquise quel que soit le résultat.

**Conditions d'émission**, toutes obligatoires :
- l'événement porte le drapeau `negRisk` de Polymarket ;
- il est **complet** : aucune de ses issues n'a été écartée au filtrage ;
- **toutes** ses issues sont cotables (deux cotations, écart inférieur à 20 points) ;
- la somme des prix moyens tient à moins de 6 points de l'unité — faute de quoi le groupe
  n'est pas la partition qu'il prétend être ;
- entre 2 et 12 issues ;
- somme des prix de vente inférieure à 0,992 ;
- gain inférieur à 6 %, au-delà duquel le signal est consigné mais **pas joué**.

**Exécution.** Toutes les jambes visent un nombre de parts identique, calé sur la jambe la
plus étroite. Signal tout-ou-rien : si une jambe ne se remplit pas, tout est abandonné.

**Ce qui la ferait abandonner.** Moins de 5 signaux exécutés en 10 jours, ou un gain réalisé
médian négatif après frais.

### c1_echelle_non_monotone — couche 1 — déclarée le 2026-07-27

**Constat exploité.** Sur une échelle de seuils **cumulatifs** — « au-dessus de 100 », « au-dessus
de 110 » — les événements s'emboîtent et la probabilité doit décroître. Une violation permet
d'acheter le seuil bas et de vendre le seuil haut pour un gain positif dans tous les cas.

**Conditions d'émission** :
- tous les libellés du groupe portent la même forme de borne unique ; un seul libellé en
  forme de tranche (« 1T–1,25T ») fait classer le groupe en partition et l'exclut ;
- violation d'au moins 1 point, et d'au plus 6 points.

**Réserve inscrite d'avance.** Ce détecteur a produit 13 faux signaux le jour de sa création,
en lisant des tranches disjointes comme des seuils emboîtés. Le classificateur est désormais
testé, mais cette stratégie reste la plus exposée à une erreur de lecture des libellés.

### c2_deribit — couche 2 — déclarée le 2026-07-27

**Constat exploité.** Polymarket cote des seuils de prix crypto. Deribit cote au même instant
des options sur le même sous-jacent, sur un carnet arbitré par des professionnels. La
probabilité risque-neutre de finir au-dessus d'un prix d'exercice vaut N(d2) de
Black-Scholes ; la nappe de volatilité est interpolée en variance totale le long du temps et
en log-moneyness le long des prix d'exercice.

**Conditions d'émission** :
- actif, sens de comparaison et seuil tous trois lisibles sans ambiguïté ; un seul montant
  doit figurer dans le libellé de groupe ;
- échéance lue dans le champ de règlement du marché, **jamais** extraite du texte ;
- le point demandé tombe à l'intérieur de la nappe cotée — aucune extrapolation ;
- écart d'au moins 3 points et d'au plus 25 points, au-delà desquels on soupçonne un
  appariement erroné plutôt qu'une occasion ;
- avantage supérieur à 1,5 fois le coût des frais.

**Abattement de confiance.** Une probabilité risque-neutre n'est pas une probabilité réelle :
elle contient une prime de risque. Seule **la moitié** de l'écart est retenue tant que les
dénouements n'ont pas mesuré la source. Ce poids sera ensuite appris, et pourra tomber à zéro.

**Réserve inscrite d'avance.** Les signaux de cette stratégie sont fortement corrélés entre
eux : une échelle BTC et une échelle ETH partageant l'échéance forment deux grappes, pas
quinze observations. Le décompte par grappes s'applique.

---

## Journal des révisions

| Date | Modification |
|---|---|
| 2026-07-27 | Création du registre. Déclaration de `c1_panier_sous_cote`, `c1_echelle_non_monotone`, `c2_deribit`. Aucune n'est `actif` : toutes commencent en `déclaré`, capital nul. |
