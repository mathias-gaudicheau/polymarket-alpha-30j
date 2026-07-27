"""Lecture et normalisation de l'univers Polymarket via l'API Gamma.

Gamma plafonne `limit` a 100 et renvoie, pour chaque marche, a la fois les
metadonnees et un premier niveau de cotation (bestBid, bestAsk, spread,
lastTradePrice). Une seule pagination fournit donc l'univers complet et
l'instantane leger : environ 21 requetes pour 2 100 marches.

Les carnets complets ne sont tires que pour les marches ou un signal se
declenche, via CLOB /books qui accepte 200 jetons par appel.
"""

from __future__ import annotations

import json

from moteur import frais as mf
from outils.commun import http_json

GAMMA = "https://gamma-api.polymarket.com"
PAS_PAGE = 100
PLAFOND_PAGES = 60  # garde-fou : 6 000 marches, bien au-dela de l'univers observe


def _json_ou_liste(valeur):
    """Gamma renvoie certains tableaux sous forme de chaine JSON."""
    if valeur is None:
        return []
    if isinstance(valeur, list):
        return valeur
    if isinstance(valeur, str):
        try:
            decode = json.loads(valeur)
            return decode if isinstance(decode, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _flottant(valeur, defaut=None):
    try:
        if valeur is None or valeur == "":
            return defaut
        return float(valeur)
    except (TypeError, ValueError):
        return defaut


def _taux_de_frais(brut: dict, categorie: str) -> tuple:
    """Determine le taux de frais applicable et d'ou il vient.

    Polymarket expose takerBaseFee par marche. L'unite n'est pas documentee :
    on accepte une valeur deja exprimee en taux (0 a 0,2) et on rejette tout
    le reste au profit de la table par categorie, plus prudente qu'une
    conversion devinee.
    """
    if brut.get("feesEnabled") is False:
        return 0.0, "marche_sans_frais"

    taker = _flottant(brut.get("takerBaseFee"))
    if taker is not None and 0.0 < taker <= 0.2:
        return taker, "api_takerBaseFee"
    if taker == 0.0:
        return 0.0, "api_takerBaseFee_nul"

    return mf.taux_de(categorie), "table_categorie"


def normaliser(brut: dict) -> dict | None:
    """Transforme un marche Gamma en enregistrement exploitable.

    Renvoie None si le marche n'est pas negociable : sans carnet ni jetons,
    il n'y a rien a simuler.
    """
    jetons = _json_ou_liste(brut.get("clobTokenIds"))
    if len(jetons) < 2 or not brut.get("enableOrderBook"):
        return None

    issues = _json_ou_liste(brut.get("outcomes"))
    prix_issues = [_flottant(p) for p in _json_ou_liste(brut.get("outcomePrices"))]

    evenements = brut.get("events") or []
    ev = evenements[0] if evenements else {}
    etiquettes = ev.get("tags") or []
    categorie = mf.categoriser(etiquettes)
    taux, source_taux = _taux_de_frais(brut, categorie)

    return {
        "id": str(brut.get("id")),
        "condition_id": brut.get("conditionId"),
        "question": brut.get("question"),
        "slug": brut.get("slug"),
        "jeton_oui": jetons[0],
        "jeton_non": jetons[1],
        "issues": issues,
        "prix_issues": prix_issues,

        "neg_risk": bool(brut.get("negRisk")),
        "neg_risk_id": brut.get("negRiskMarketID"),
        "groupe_titre": brut.get("groupItemTitle"),
        "groupe_seuil": _flottant(brut.get("groupItemThreshold")),

        "evenement_id": str(ev.get("id")) if ev.get("id") is not None else None,
        "evenement_slug": ev.get("slug"),
        "evenement_titre": ev.get("title"),
        "etiquettes": [t.get("slug") for t in etiquettes if isinstance(t, dict)],
        "categorie": categorie,
        "taux_frais": taux,
        "source_taux": source_taux,

        "achat": _flottant(brut.get("bestBid")),
        "vente": _flottant(brut.get("bestAsk")),
        "ecart": _flottant(brut.get("spread")),
        "dernier": _flottant(brut.get("lastTradePrice")),
        "liquidite": _flottant(brut.get("liquidityNum") or brut.get("liquidity"), 0.0),
        "volume24": _flottant(brut.get("volume24hr"), 0.0),
        "volume": _flottant(brut.get("volumeNum") or brut.get("volume"), 0.0),

        "taille_min": _flottant(brut.get("orderMinSize"), 5.0),
        "pas_de_prix": _flottant(brut.get("orderPriceMinTickSize"), 0.001),
        "ecart_max_prime": _flottant(brut.get("rewardsMaxSpread")),
        "taille_min_prime": _flottant(brut.get("rewardsMinSize")),

        "accepte_ordres": bool(brut.get("acceptingOrders")),
        "actif": bool(brut.get("active")),
        "clos": bool(brut.get("closed")),
        "fin": brut.get("endDateIso") or brut.get("endDate"),
        "debut": brut.get("startDateIso") or brut.get("startDate"),
        "cree": brut.get("createdAt"),
        "statut_uma": brut.get("umaResolutionStatuses"),
    }


def lire_univers(inclure_clos=False, journal=None) -> tuple:
    """Pagine l'integralite des marches et renvoie (marches, diagnostic)."""
    marches, decalage, pages = [], 0, 0
    rejetes_sans_carnet = 0
    echecs = []

    while pages < PLAFOND_PAGES:
        rep = http_json(GAMMA + "/markets", {
            "closed": "true" if inclure_clos else "false",
            "limit": PAS_PAGE,
            "offset": decalage,
        }, essais=3)

        if not rep.ok:
            # Gamma refuse les decalages au-dela de l'univers avec un 422
            # explicite. Ce n'est pas une panne : c'est la fin de la liste.
            fin_normale = (rep.statut == 422
                           and "offset too large" in (rep.erreur or ""))
            if not fin_normale:
                echecs.append({"decalage": decalage, "erreur": rep.erreur,
                               "statut": rep.statut})
            break
        if not isinstance(rep.donnees, list) or not rep.donnees:
            break

        for brut in rep.donnees:
            enr = normaliser(brut)
            if enr is None:
                rejetes_sans_carnet += 1
            else:
                marches.append(enr)

        pages += 1
        if len(rep.donnees) < PAS_PAGE:
            break
        decalage += PAS_PAGE

    diagnostic = {
        "pages_lues": pages,
        "marches_retenus": len(marches),
        "rejetes_sans_carnet": rejetes_sans_carnet,
        "echecs": echecs,
        "pagination_tronquee": pages >= PLAFOND_PAGES,
    }
    if journal:
        journal("univers : %d marches retenus, %d ecartes, %d pages, %d echec(s)"
                % (len(marches), rejetes_sans_carnet, pages, len(echecs)))
    return marches, diagnostic


def grouper_par_evenement(marches) -> dict:
    """Regroupe par evenement : base des detecteurs de somme et de dominance."""
    groupes = {}
    for m in marches:
        cle = m.get("evenement_id") or m.get("neg_risk_id") or ("solo:" + m["id"])
        groupes.setdefault(cle, []).append(m)
    return groupes
