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


def normaliser(brut: dict, evenement=None) -> dict | None:
    """Transforme un marche Gamma en enregistrement exploitable.

    Renvoie None si le marche n'est pas negociable : sans carnet ni jetons,
    il n'y a rien a simuler.

    L'evenement porteur est passe explicitement quand on pagine sur /events :
    c'est lui qui detient les etiquettes, et donc le taux de frais applicable.
    La pagination sur /markets ne les fournit pas, ce qui faisait tomber tous
    les marches dans la categorie inconnue et donc au taux le plus cher.
    """
    jetons = _json_ou_liste(brut.get("clobTokenIds"))
    if len(jetons) < 2 or not brut.get("enableOrderBook"):
        return None

    # Un evenement ouvert contient aussi ses marches deja clotures. Leurs prix
    # sont figes a 0 ou 1 et fabriqueraient des incoherences imaginaires : on
    # les ecarte ici, au plus pres de la source.
    if brut.get("closed") or brut.get("archived"):
        return None
    if brut.get("active") is False or brut.get("acceptingOrders") is False:
        return None

    issues = _json_ou_liste(brut.get("outcomes"))
    prix_issues = [_flottant(p) for p in _json_ou_liste(brut.get("outcomePrices"))]

    ev = evenement
    if ev is None:
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
    """Pagine l'integralite de l'univers et renvoie (marches, diagnostic).

    On pagine sur /events plutot que sur /markets : un evenement porte a la
    fois ses marches et ses etiquettes, ce qui donne en une seule passe la
    cotation et la categorie de frais. Paginer sur /markets prive de ces
    etiquettes et fait facturer tout l'univers au taux le plus cher, ce qui
    condamnerait a tort la quasi-totalite des occasions.
    """
    marches, evenements, decalage, pages = [], [], 0, 0
    rejetes_sans_carnet = 0
    echecs = []
    evenements_lus = 0

    while pages < PLAFOND_PAGES:
        rep = http_json(GAMMA + "/events", {
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

        for ev in rep.donnees:
            evenements_lus += 1
            bruts = ev.get("markets") or []
            retenus = []
            for brut in bruts:
                enr = normaliser(brut, evenement=ev)
                if enr is None:
                    rejetes_sans_carnet += 1
                else:
                    retenus.append(enr)
                    marches.append(enr)
            if retenus:
                evenements.append({
                    "id": str(ev.get("id")),
                    "slug": ev.get("slug"),
                    "titre": ev.get("title"),
                    "neg_risk": bool(ev.get("negRisk") or ev.get("enableNegRisk")),
                    "etiquettes": [t.get("slug") for t in (ev.get("tags") or [])
                                   if isinstance(t, dict)],
                    # Decisif pour l'exhaustivite : combien de marches
                    # l'evenement portait, et combien ont survecu au filtrage.
                    "marches_annonces": len(bruts),
                    "marches": retenus,
                    "complet": len(retenus) == len(bruts),
                })

        pages += 1
        if len(rep.donnees) < PAS_PAGE:
            break
        decalage += PAS_PAGE

    # Un marche peut apparaitre dans deux evenements : on ne le garde qu'une fois.
    vus, uniques = set(), []
    for m in marches:
        if m["id"] in vus:
            continue
        vus.add(m["id"])
        uniques.append(m)

    connues = sum(1 for m in uniques if m["categorie"] != "inconnu")
    complets = sum(1 for e in evenements if e["complet"])
    diagnostic = {
        "pages_lues": pages,
        "evenements_lus": evenements_lus,
        "evenements_retenus": len(evenements),
        "evenements_complets": complets,
        "marches_retenus": len(uniques),
        "doublons_ecartes": len(marches) - len(uniques),
        "rejetes_sans_carnet": rejetes_sans_carnet,
        "categorie_resolue": connues,
        "taux_categorie_resolue": round(connues / max(1, len(uniques)), 3),
        "echecs": echecs,
        "pagination_tronquee": pages >= PLAFOND_PAGES,
    }
    if journal:
        journal("univers : %d marches negociables sur %d evenements lus, "
                "%d ecartes (clos ou sans carnet), categorie resolue a %.0f%%"
                % (len(uniques), evenements_lus, rejetes_sans_carnet,
                   100 * diagnostic["taux_categorie_resolue"]))
        journal("evenements exploitables : %d, dont %d complets"
                % (len(evenements), complets))
    return uniques, evenements, diagnostic


def grouper_par_evenement(marches) -> dict:
    """Regroupe par evenement : base des detecteurs de somme et de dominance."""
    groupes = {}
    for m in marches:
        cle = m.get("evenement_id") or m.get("neg_risk_id") or ("solo:" + m["id"])
        groupes.setdefault(cle, []).append(m)
    return groupes
