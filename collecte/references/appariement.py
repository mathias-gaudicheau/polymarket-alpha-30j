"""Appariement des marches Polymarket avec les references externes.

L'appariement est l'endroit ou tout peut deraper en silence. Confondre deux
marches voisins produit un ecart de prix spectaculaire et entierement faux.
La regle suivie ici est donc de refuser plutot que de deviner : un marche
qu'on ne sait pas apparier avec certitude est laisse de cote et consigne,
pour etre examine plus tard.

Deux choix concrets vont dans ce sens.

La date de reglement est lue dans le champ `fin` du marche, jamais extraite
du libelle. Un libelle dit "d'ici juillet" quand le reglement tombe le 31 a
midi ; se fier au texte introduirait une erreur de plusieurs jours sur une
echeance courte, ce qui suffit a fausser toute probabilite.

Le sens de comparaison doit etre explicite. Un marche dont on ne sait pas
s'il paie au-dessus ou en dessous du seuil n'est pas apparie.
"""

from __future__ import annotations

import calendar
import re
import time

# Reconnaissance de l'actif.
_ACTIFS = {
    "BTC": re.compile(r"\b(bitcoin|btc)\b", re.IGNORECASE),
    "ETH": re.compile(r"\b(ethereum|ether|eth)\b", re.IGNORECASE),
}

# Un montant : 120000, 120,000, $120K, 120k, 1.2M.
_MONTANT = re.compile(
    r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|m|b)?\b", re.IGNORECASE)

_AU_DESSUS = re.compile(
    r"\b(above|over|reach|hit|exceed|greater|higher|surpass|at least|more than|top)\b",
    re.IGNORECASE)
_EN_DESSOUS = re.compile(
    r"\b(below|under|dip|fall|drop|less than|lower than|down to)\b",
    re.IGNORECASE)

_MULTIPLICATEUR = {"k": 1e3, "m": 1e6, "b": 1e9, None: 1.0, "": 1.0}

# En deca, un seuil crypto n'est pas un prix mais un numero de semaine, un
# pourcentage ou une annee.
SEUIL_MINIMAL = {"BTC": 1000.0, "ETH": 50.0}


def _en_horodatage(texte):
    """Convertit une date ISO en secondes depuis l'epoque."""
    if not texte:
        return None
    for forme in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d"):
        try:
            return calendar.timegm(time.strptime(texte[:26], forme))
        except (ValueError, TypeError):
            continue
    return None


def _montants(texte):
    """Tous les montants lisibles d'un texte, en valeur absolue."""
    sortie = []
    for m in _MONTANT.finditer(texte or ""):
        try:
            valeur = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        suffixe = (m.group(2) or "").lower()
        sortie.append(valeur * _MULTIPLICATEUR.get(suffixe, 1.0))
    return sortie


def extraire_seuil_crypto(marche):
    """Identifie (devise, seuil, sens) sur un marche crypto a seuil.

    Renvoie None des que le moindre element manque : mieux vaut ne rien
    apparier qu'apparier de travers.
    """
    texte = " ".join(str(marche.get(c) or "")
                     for c in ("question", "groupe_titre", "evenement_titre"))
    if not texte.strip():
        return None

    devise = None
    for symbole, motif in _ACTIFS.items():
        if motif.search(texte):
            devise = symbole
            break
    if devise is None:
        return None

    # Le sens doit etre univoque.
    haut, bas = bool(_AU_DESSUS.search(texte)), bool(_EN_DESSOUS.search(texte))
    if haut == bas:
        return None
    sens = "au_dessus" if haut else "en_dessous"

    # Le seuil : on privilegie le libelle de groupe, plus specifique que la
    # question, qui contient souvent d'autres nombres (annee, echeance).
    candidats = _montants(marche.get("groupe_titre")) or _montants(marche.get("question"))
    plancher = SEUIL_MINIMAL.get(devise, 0.0)
    candidats = [v for v in candidats if v >= plancher]
    if len(candidats) != 1:
        # Zero : aucun prix lisible. Plusieurs : ambigu, on s'abstient.
        return None

    echeance = _en_horodatage(marche.get("fin"))
    if echeance is None or echeance <= time.time():
        return None

    return {"devise": devise, "seuil": candidats[0], "sens": sens,
            "echeance": echeance}


def apparier_crypto(marches, surfaces, journal=None):
    """Confronte les marches crypto a seuil a la surface d'options.

    Renvoie (appariements, diagnostic). Le diagnostic liste ce qui n'a pas pu
    etre apparie et pourquoi : c'est lui qui permettra d'elargir la couverture
    sans jamais relacher l'exigence.
    """
    from collecte.references import deribit

    appariements = []
    motifs = {"pas_crypto": 0, "seuil_illisible": 0, "hors_nappe": 0,
              "devise_absente": 0, "apparies": 0}
    exemples_rejetes = []

    for m in marches:
        etiquettes = m.get("etiquettes") or []
        parait_crypto = (m.get("categorie") == "crypto"
                         or any("crypto" in str(e) or "bitcoin" in str(e)
                                or "ethereum" in str(e) for e in etiquettes))
        if not parait_crypto:
            motifs["pas_crypto"] += 1
            continue

        seuil = extraire_seuil_crypto(m)
        if seuil is None:
            motifs["seuil_illisible"] += 1
            if len(exemples_rejetes) < 12:
                exemples_rejetes.append({
                    "question": (m.get("question") or "")[:90],
                    "groupe": m.get("groupe_titre"),
                })
            continue

        surface = surfaces.get(seuil["devise"])
        if surface is None:
            motifs["devise_absente"] += 1
            continue

        proba, detail = deribit.proba_au_dessus(
            surface, seuil["seuil"], seuil["echeance"])
        if proba is None:
            motifs["hors_nappe"] += 1
            continue

        # Le marche cote la probabilite de son propre libelle. Si celui-ci se
        # lit "en dessous", la probabilite recherchee est le complement.
        if seuil["sens"] == "en_dessous":
            proba = 1.0 - proba

        motifs["apparies"] += 1
        appariements.append({
            "marche": m, "source": "deribit", "proba": proba,
            "seuil": seuil, "detail": detail,
        })

    if journal:
        journal("appariement crypto : %d apparies, %d seuils illisibles, "
                "%d hors nappe" % (motifs["apparies"], motifs["seuil_illisible"],
                                   motifs["hors_nappe"]))
        for ex in exemples_rejetes[:5]:
            journal("   non apparie : %s | groupe=%s"
                    % (ex["question"], ex["groupe"]))

    return appariements, {"motifs": motifs, "exemples_rejetes": exemples_rejetes}
