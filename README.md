# Polymarket — mesure d'alpha sur 30 jours

Expérience de recherche : mesurer si les erreurs de cotation observables sur Polymarket
sont réellement monnayables, une fois les frais et la profondeur réelle des carnets pris
en compte.

## Ce que ce dépôt est, et ce qu'il n'est pas

**C'est** un simulateur. Il lit des cotations publiques, détecte des incohérences, prend
des décisions horodatées et tient une comptabilité en capital virtuel de 500 $.

**Ce n'est pas** un robot de trading. Aucun compte n'est créé, aucun fonds n'est déposé,
aucun ordre n'est passé. Aucune clé d'exécution n'existe dans ce dépôt et le code n'a
aucun chemin permettant d'en utiliser une.

Polymarket est bloqué en France depuis le 16 juillet 2026 par décision de l'ANJ. Y placer
de l'argent réel depuis la France serait illégal. C'est précisément pourquoi cette
expérience est une simulation.

## Pourquoi le dépôt est public

L'objet de la mission est une **preuve** d'alpha, pas une affirmation. Une stratégie
inscrite dans l'historique git est datée et scellée : elle ne peut pas être réécrite après
avoir vu le résultat. C'est ce qui distingue une mesure d'une justification après coup.

Chaque stratégie est déclarée dans `PROTOCOLE.md` **avant** que la moindre décision ne lui
soit attribuée. Le commit fait foi.

## Méthode

Trois couches alimentent un même pipeline de décision :

1. **Détecteurs** — incohérences arithmétiques internes (sommes qui ne font pas 1,
   échelles de seuils non monotones, dominances conditionnelles violées).
2. **Références externes** — probabilités issues de marchés plus efficients (options
   Deribit, consensus des bookmakers, Kalshi, modèles météo d'ensemble).
3. **Découverte** — fouille automatique de l'historique accumulé, sous correction pour
   tests multiples.

Les décisions sont ensuite filtrées par un modèle d'exécution volontairement pessimiste :
remplissage au prix adverse réel dans la limite de la profondeur disponible, frais déduits,
et cinq minutes de latence imposées entre le signal et l'exécution.

Un **témoin aléatoire** double chaque décision par un pari tiré au hasard dans le même
univers. Sans écart net au témoin, il n'y a pas d'alpha — seulement de la chance.

## Structure

| Dossier | Rôle |
|---|---|
| `sonde/` | Relevé des schémas et limites réels des sources de données |
| `outils/` | Utilitaires partagés |
| `collecte/` | Univers suivi, instantanés de carnets, références externes |
| `detecteurs/` | Couche 1 |
| `decouverte/` | Couche 3 et correction statistique |
| `moteur/` | Exécution simulée, portefeuille, allocation |
| `analyse/` | Métriques et rapport |

## Licence et usage

Code de recherche, fourni tel quel. Il ne constitue pas un conseil en investissement.
