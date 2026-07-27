"""Couche 1 : les incoherences arithmetiques internes.

Ces detecteurs n'emettent aucun pronostic. Ils constatent qu'un ensemble de
prix viole une contrainte que l'algebre impose, quel que soit l'avenir.

Une remarque importante sur le detecteur OUI/NON. Sur Polymarket les deux
carnets d'un meme marche sont lies par construction : le meilleur achat du
NON vaut exactement un moins la meilleure vente du OUI. L'arbitrage interne
y est donc structurellement impossible, et un detecteur qui le chercherait
ne se declencherait jamais. On le conserve quand meme, non comme strategie
mais comme temoin de qualite des donnees : s'il se declenche, c'est notre
lecture qui est fausse, pas le marche qui est genereux.
"""

from __future__ import annotations

import re

from detecteurs.socle import Jambe, Signal, prix_negociables

# Un panier ne vaut la peine que si l'ecart depasse nettement le bruit de tick.
MARGE_PANIER = 0.008
MARGE_ECHELLE = 0.010

# Au-dela, un groupe est trop grand pour que toutes les jambes soient
# executables ensemble de facon credible.
JAMBES_MAXIMALES = 12

# Ecart tolere entre la somme des prix moyens d'une partition et l'unite.
# Un ensemble d'issues exclusives et exhaustives somme necessairement a un,
# aux frottements pres. S'en eloigner trahit un groupe incomplet.
TOLERANCE_PARTITION = 0.06

# Garde-fou d'incredulite. Un gain sans risque de plus de six pour cent sur
# un marche public serait ramasse en quelques secondes par des dizaines de
# robots. En observer un signifie presque toujours que notre modele se trompe,
# pas que le marche est genereux. On le consigne, on ne le joue pas.
PLAFOND_GAIN_CREDIBLE = 0.06


# --------------------------------------------------------------------------
# Temoin de qualite : l'arbitrage OUI/NON ne doit jamais se declencher
# --------------------------------------------------------------------------

def temoin_oui_non(marches) -> list:
    """Ne renvoie que des anomalies de donnees, jamais des occasions.

    Sur un marche sain : achat <= vente. Un croisement signale une lecture
    corrompue, une cotation perimee ou un marche en cours de reglement.
    """
    anomalies = []
    for m in marches:
        a, v = m.get("achat"), m.get("vente")
        if a is None or v is None:
            continue
        if v < a - 1e-9:
            anomalies.append({
                "marche_id": m["id"],
                "question": m.get("question"),
                "achat": a, "vente": v,
                "motif": "carnet croise : achat %.4f > vente %.4f" % (a, v),
            })
    return anomalies


# --------------------------------------------------------------------------
# Detecteur 2 : la somme d'un panier a issues exclusives
# --------------------------------------------------------------------------

def paniers_negrisk(evenements, journal=None) -> tuple:
    """Sur un evenement negRisk, exactement une issue se realise.

    Deux occasions symetriques :

      somme des meilleures ventes < 1  -> acheter tout le panier coute moins
                                          d'un dollar et en rapporte un
      somme des meilleurs achats  > 1  -> vendre tout le panier encaisse plus
                                          d'un dollar et n'en coute qu'un

    Tout repose sur un mot : *tout* le panier. Ce raisonnement n'a de sens que
    si le groupe contient l'integralite des issues. Deux candidats sur
    cinquante sommeront toujours a bien moins d'un dollar sans que cela ne
    constitue le moindre arbitrage. Une premiere version de ce detecteur
    filtrait les marches non cotables avant de constituer les groupes, ce qui
    detruisait l'exhaustivite et fabriquait des occasions imaginaires a 17 %
    de rendement pretendument garanti.

    Trois verrous, donc, avant d'emettre quoi que ce soit :
      - l'evenement doit etre complet, aucun marche perdu au filtrage
      - toutes ses issues doivent etre cotables, sans exception
      - la somme des prix moyens doit deja tourner autour de un, faute de
        quoi le groupe n'est pas la partition qu'il pretend etre
    """
    signaux, ecartes = [], {"incomplet": 0, "issue_non_cotable": 0,
                            "somme_implausible": 0, "gain_incroyable": 0,
                            "trop_de_jambes": 0}

    for ev in evenements:
        if not ev.get("neg_risk"):
            continue
        lot = ev.get("marches") or []
        if len(lot) < 2:
            continue
        if len(lot) > JAMBES_MAXIMALES:
            ecartes["trop_de_jambes"] += 1
            continue

        # Verrou 1 : aucune issue n'a disparu au filtrage.
        if not ev.get("complet"):
            ecartes["incomplet"] += 1
            continue

        # Verrou 2 : toutes les issues sont cotables.
        if not all(prix_negociables(m) for m in lot):
            ecartes["issue_non_cotable"] += 1
            continue

        # Verrou 3 : la partition ressemble a une partition.
        somme_milieux = sum((m["achat"] + m["vente"]) / 2.0 for m in lot)
        if not (1.0 - TOLERANCE_PARTITION <= somme_milieux <= 1.0 + TOLERANCE_PARTITION):
            ecartes["somme_implausible"] += 1
            continue

        somme_ventes = sum(m["vente"] for m in lot)
        somme_achats = sum(m["achat"] for m in lot)

        # Sous-cote : le panier complet s'achete pour moins d'un dollar.
        if somme_ventes < 1.0 - MARGE_PANIER:
            gain = 1.0 - somme_ventes
            if gain > PLAFOND_GAIN_CREDIBLE:
                ecartes["gain_incroyable"] += 1
            else:
                jambes = [
                    Jambe(m["id"], m["jeton_oui"], "achat", m["vente"],
                          m["categorie"], m["taux_frais"],
                          libelle=m.get("groupe_titre") or m.get("question"))
                    for m in lot
                ]
                signaux.append(Signal(
                    "c1_panier_sous_cote", jambes, gain,
                    "Somme des ventes = %.4f sur les %d issues exclusives de "
                    "l'evenement %s. Acheter le panier entier coute %.4f $ et "
                    "rapporte exactement 1,00 $."
                    % (somme_ventes, len(lot), ev.get("slug"), somme_ventes),
                    tout_ou_rien=True, gain_certain=gain,
                    marche_pivot=lot[0]["id"]))

        # Sur-cote : le panier complet se vend pour plus d'un dollar.
        if somme_achats > 1.0 + MARGE_PANIER:
            gain = somme_achats - 1.0
            if gain > PLAFOND_GAIN_CREDIBLE:
                ecartes["gain_incroyable"] += 1
            else:
                jambes = [
                    Jambe(m["id"], m["jeton_non"], "achat",
                          round(1.0 - m["achat"], 6),
                          m["categorie"], m["taux_frais"],
                          libelle="NON " + (m.get("groupe_titre")
                                            or m.get("question") or ""))
                    for m in lot
                ]
                signaux.append(Signal(
                    "c1_panier_sur_cote", jambes, gain,
                    "Somme des achats = %.4f sur les %d issues exclusives de "
                    "l'evenement %s. Vendre le panier entier encaisse %.4f $ "
                    "pour un seul dollar a verser."
                    % (somme_achats, len(lot), ev.get("slug"), somme_achats),
                    tout_ou_rien=True, gain_certain=gain,
                    marche_pivot=lot[0]["id"]))

    if journal:
        journal("paniers negRisk : %d signaux ; ecartes -> %s"
                % (len(signaux), ", ".join("%s=%d" % kv for kv in ecartes.items()
                                           if kv[1])))
    return signaux, ecartes


# --------------------------------------------------------------------------
# Detecteur 3 : les echelles de seuils doivent etre monotones
# --------------------------------------------------------------------------

_SENS_CROISSANT = re.compile(
    r"\b(above|over|at least|or more|greater than|higher than|exceed|≥|>=|>)\b",
    re.IGNORECASE)
_SENS_DECROISSANT = re.compile(
    r"\b(below|under|at most|or less|less than|lower than|≤|<=|<)\b",
    re.IGNORECASE)


def _sens_echelle(marches) -> str | None:
    """Determine si le seuil se lit 'au-dessus de' ou 'en dessous de'.

    En cas d'ambiguite on renvoie None et l'echelle est ignoree : se tromper
    de sens transformerait un arbitrage en pari a l'envers.
    """
    croissants = decroissants = 0
    for m in marches:
        texte = " ".join(str(m.get(c) or "") for c in ("groupe_titre", "question"))
        if _SENS_CROISSANT.search(texte):
            croissants += 1
        if _SENS_DECROISSANT.search(texte):
            decroissants += 1
    if croissants and not decroissants:
        return "au_dessus"
    if decroissants and not croissants:
        return "en_dessous"
    return None


def echelles_de_seuils(marches, journal=None) -> list:
    """P(X au-dessus de t) doit decroitre quand t augmente.

    Si le marche a 110 cote plus cher que le marche a 100, on achete le 100
    et on vend le 110. Quel que soit le resultat, le gain est positif ou nul,
    et strictement positif dans la tranche intermediaire.
    """
    groupes = {}
    for m in marches:
        if m.get("groupe_seuil") is None or not m.get("evenement_id"):
            continue
        if not prix_negociables(m):
            continue
        groupes.setdefault(m["evenement_id"], []).append(m)

    signaux, examines, invraisemblables = [], 0, 0
    for _cle, lot in groupes.items():
        if len(lot) < 2:
            continue
        sens = _sens_echelle(lot)
        if sens is None:
            continue
        examines += 1

        # Range du seuil le plus bas au plus haut.
        lot = sorted(lot, key=lambda m: m["groupe_seuil"])
        if sens == "en_dessous":
            lot = list(reversed(lot))
        # Desormais la probabilite attendue decroit le long de la liste.

        for i in range(len(lot) - 1):
            haut, bas = lot[i], lot[i + 1]   # haut doit coter >= bas
            # Violation : le suivant se vend plus cher qu'on ne peut acheter
            # le precedent.
            if bas["achat"] > haut["vente"] + MARGE_ECHELLE:
                gain = bas["achat"] - haut["vente"]
                if gain > PLAFOND_GAIN_CREDIBLE:
                    # Une monotonie violee de plus de six points sur deux
                    # marches reellement emboites n'existe pas : c'est notre
                    # lecture du sens de l'echelle qui est fausse.
                    invraisemblables += 1
                    continue
                jambes = [
                    Jambe(haut["id"], haut["jeton_oui"], "achat", haut["vente"],
                          haut["categorie"], haut["taux_frais"],
                          libelle="achat seuil %s" % haut.get("groupe_titre")),
                    Jambe(bas["id"], bas["jeton_non"], "achat",
                          round(1.0 - bas["achat"], 6),
                          bas["categorie"], bas["taux_frais"],
                          libelle="vente seuil %s" % bas.get("groupe_titre")),
                ]
                signaux.append(Signal(
                    "c1_echelle_non_monotone", jambes, gain,
                    "Seuil %s cote %.4f a l'achat alors que le seuil %s, moins "
                    "exigeant, se vend a %.4f. La monotonie est violee de %.4f."
                    % (bas.get("groupe_titre"), bas["achat"],
                       haut.get("groupe_titre"), haut["vente"], gain),
                    tout_ou_rien=True, gain_certain=gain,
                    marche_pivot=haut["id"]))

    if journal:
        journal("echelles de seuils : %d echelles lisibles, %d signaux, "
                "%d ecarts juges invraisemblables"
                % (examines, len(signaux), invraisemblables))
    return signaux


# --------------------------------------------------------------------------
# Detecteur 9 : la distribution implicite d'une echelle
# --------------------------------------------------------------------------

def tranches_aberrantes(marches, journal=None) -> list:
    """Reconstruit la densite implicite d'une echelle et repere les creux.

    Entre deux seuils consecutifs, la difference des probabilites cumulees
    donne la probabilite de la tranche. Cette densite ne peut pas etre
    negative : une tranche negative est une incoherence stricte, et pas une
    simple opinion sur la forme de la distribution.

    On se limite volontairement a ce constat. Juger qu'une densite est
    "trop creuse" supposerait un modele de la vraie distribution, ce qui
    n'est plus de l'arithmetique mais du pronostic : cela releve de la
    couche 3, sous controle statistique.
    """
    groupes = {}
    for m in marches:
        if m.get("groupe_seuil") is None or not m.get("evenement_id"):
            continue
        if not prix_negociables(m):
            continue
        groupes.setdefault(m["evenement_id"], []).append(m)

    signaux = []
    for _cle, lot in groupes.items():
        if len(lot) < 3:
            continue
        sens = _sens_echelle(lot)
        if sens is None:
            continue
        lot = sorted(lot, key=lambda m: m["groupe_seuil"])
        if sens == "en_dessous":
            lot = list(reversed(lot))

        milieux = [(m["achat"] + m["vente"]) / 2.0 for m in lot]
        for i in range(len(lot) - 1):
            densite = milieux[i] - milieux[i + 1]
            if densite < -MARGE_ECHELLE:
                signaux.append({
                    "type": "densite_negative",
                    "evenement_id": lot[i].get("evenement_id"),
                    "entre": [lot[i].get("groupe_titre"), lot[i + 1].get("groupe_titre")],
                    "densite": densite,
                })

    if journal:
        journal("densites implicites : %d tranches negatives reperees" % len(signaux))
    return signaux


def tous_les_detecteurs(marches, evenements, journal=None):
    """Passe l'univers dans toute la couche 1.

    Renvoie (signaux, diagnostics). Les diagnostics ne sont pas des occasions
    mais des indicateurs de sante des donnees, a surveiller au point d'etape.
    """
    signaux = []
    paniers, ecartes_paniers = paniers_negrisk(evenements, journal)
    signaux.extend(paniers)
    signaux.extend(echelles_de_seuils(marches, journal))

    diagnostics = {
        "carnets_croises": temoin_oui_non(marches),
        "densites_negatives": tranches_aberrantes(marches, journal),
        "paniers_ecartes": ecartes_paniers,
    }
    return signaux, diagnostics
