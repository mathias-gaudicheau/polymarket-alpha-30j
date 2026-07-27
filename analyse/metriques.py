"""Mesures statistiques de l'experience.

Calculs purs, sans acces reseau ni base : ils doivent etre testables au
centime pres.

Une notion domine tout le fichier, celle de **grappe**. Quinze positions
prises sur une meme echelle de seuils bitcoin ne constituent pas quinze
observations : elles se denouent ensemble, elles gagnent ou perdent ensemble.
Les traiter comme independantes reduirait artificiellement les intervalles de
confiance d'un facteur proche de la racine de quinze, soit pres de quatre.
C'est la maniere la plus commode de se convaincre qu'on a trouve quelque
chose. Tout ce qui suit raisonne donc en grappes, jamais en paris.
"""

from __future__ import annotations

import math
import random


def brier(previsions) -> float | None:
    """Score de Brier : moyenne des carres d'ecart entre proba et realite.

    Plus bas vaut mieux. Un pronostiqueur qui annonce toujours 0,5 obtient
    0,25 ; c'est le repere a battre.
    """
    valides = [(p, r) for p, r in previsions if p is not None and r is not None]
    if not valides:
        return None
    return sum((p - (1.0 if r else 0.0)) ** 2 for p, r in valides) / len(valides)


def calibration(previsions, nb_tranches=10):
    """Frequence reellement observee par tranche de probabilite annoncee.

    Un pronostiqueur calibre voit ses paris annonces a 20 % se realiser
    environ 20 % du temps. L'ecart par tranche est directement monnayable.
    """
    tranches = []
    for i in range(nb_tranches):
        bas, haut = i / nb_tranches, (i + 1) / nb_tranches
        lot = [(p, r) for p, r in previsions
               if p is not None and bas <= p < haut + (1e-9 if i == nb_tranches - 1 else 0)]
        if not lot:
            continue
        tranches.append({
            "de": round(bas, 2), "a": round(haut, 2),
            "n": len(lot),
            "annonce_moyen": sum(p for p, _ in lot) / len(lot),
            "realise": sum(1 for _, r in lot if r) / len(lot),
        })
    return tranches


def grouper_en_grappes(positions, cle=None):
    """Regroupe les positions qui se denouent ensemble.

    Par defaut, deux positions appartiennent a la meme grappe si elles
    partagent le meme evenement. Faute d'evenement, le marche fait foi.
    """
    def cle_defaut(p):
        return p.get("grappe") or p.get("evenement_id") or ("m:%s" % p.get("marche_id"))

    cle = cle or cle_defaut
    grappes = {}
    for p in positions:
        grappes.setdefault(cle(p), []).append(p)
    return grappes


def rendement_par_grappe(positions, cle=None):
    """Rendement agrege de chaque grappe : (somme des gains, somme des mises)."""
    grappes = grouper_en_grappes(positions, cle)
    sortie = []
    for identifiant, lot in grappes.items():
        mise = sum(abs(p.get("mise") or 0.0) for p in lot)
        gain = sum(p.get("gain") or 0.0 for p in lot)
        if mise <= 0:
            continue
        sortie.append({"grappe": identifiant, "n_positions": len(lot),
                       "mise": mise, "gain": gain, "rendement": gain / mise})
    return sortie


def intervalle_bootstrap(valeurs, niveau=0.95, tirages=4000, graine=1):
    """Intervalle de confiance par reechantillonnage.

    Aucune hypothese de normalite : les rendements de paris binaires sont
    fortement asymetriques, une formule gaussienne y serait trompeuse.
    """
    valeurs = [v for v in valeurs if v is not None]
    n = len(valeurs)
    if n < 2:
        return None
    alea = random.Random(graine)
    moyennes = []
    for _ in range(tirages):
        echantillon = [valeurs[alea.randrange(n)] for _ in range(n)]
        moyennes.append(sum(echantillon) / n)
    moyennes.sort()
    marge = (1.0 - niveau) / 2.0
    bas = moyennes[int(marge * tirages)]
    haut = moyennes[min(tirages - 1, int((1.0 - marge) * tirages))]
    return {"moyenne": sum(valeurs) / n, "bas": bas, "haut": haut,
            "n": n, "niveau": niveau}


def comparer_au_temoin(rendements_reels, rendements_temoin, tirages=4000, graine=2):
    """Ecart au temoin aleatoire, avec son intervalle.

    C'est la seule mesure qui distingue un avantage d'une derive generale du
    marche. Si les deux series rapportent pareil, il n'y a pas d'alpha : il y
    a eu une periode favorable.
    """
    if len(rendements_reels) < 2 or len(rendements_temoin) < 2:
        return None
    alea = random.Random(graine)
    nr, nt = len(rendements_reels), len(rendements_temoin)
    ecarts = []
    for _ in range(tirages):
        a = sum(rendements_reels[alea.randrange(nr)] for _ in range(nr)) / nr
        b = sum(rendements_temoin[alea.randrange(nt)] for _ in range(nt)) / nt
        ecarts.append(a - b)
    ecarts.sort()
    moyenne_reelle = sum(rendements_reels) / nr
    moyenne_temoin = sum(rendements_temoin) / nt
    return {
        "reel": moyenne_reelle,
        "temoin": moyenne_temoin,
        "ecart": moyenne_reelle - moyenne_temoin,
        "bas": ecarts[int(0.025 * tirages)],
        "haut": ecarts[int(0.975 * tirages)],
        "n_reel": nr, "n_temoin": nt,
        "significatif": ecarts[int(0.025 * tirages)] > 0,
    }


def benjamini_hochberg(valeurs_p, seuil=0.10):
    """Controle du taux de fausses decouvertes.

    Indispensable des lors qu'on teste beaucoup d'hypotheses : sur mille
    tests sans le moindre effet reel, cinquante paraissent significatifs au
    seuil de cinq pour cent. Sans cette correction, un moteur de decouverte
    n'est qu'une machine a fabriquer des illusions rentables sur le papier.

    Renvoie la liste des indices retenus.
    """
    if not valeurs_p:
        return []
    indexes = sorted(range(len(valeurs_p)), key=lambda i: valeurs_p[i])
    m = len(valeurs_p)
    limite = -1
    for rang, i in enumerate(indexes, start=1):
        if valeurs_p[i] <= seuil * rang / m:
            limite = rang
    return sorted(indexes[:limite]) if limite > 0 else []


def valeur_p_binomiale(succes, essais, proba):
    """Probabilite d'obtenir au moins autant de succes par pur hasard.

    Test unilateral exact, sans approximation normale : les effectifs sont
    souvent trop petits pour qu'elle tienne.
    """
    if essais <= 0:
        return 1.0
    if succes <= 0:
        return 1.0
    total = 0.0
    for k in range(succes, essais + 1):
        total += (math.comb(essais, k) * (proba ** k) * ((1.0 - proba) ** (essais - k)))
    return min(1.0, max(0.0, total))


def resume_strategie(positions, positions_temoin=None, nom=""):
    """Tableau de bord d'une strategie, en grappes et non en paris."""
    grappes = rendement_par_grappe(positions)
    rendements = [g["rendement"] for g in grappes]
    mise_totale = sum(abs(p.get("mise") or 0.0) for p in positions)
    gain_total = sum(p.get("gain") or 0.0 for p in positions)

    resume = {
        "strategie": nom,
        "n_paris": len(positions),
        "n_grappes": len(grappes),
        "mise_totale": round(mise_totale, 4),
        "gain_total": round(gain_total, 4),
        "rendement_global": (gain_total / mise_totale) if mise_totale > 0 else None,
        "intervalle": intervalle_bootstrap(rendements),
        "temoin": None,
        "conclusion_permise": len(grappes) >= 30,
    }

    if positions_temoin:
        grappes_t = rendement_par_grappe(positions_temoin)
        resume["temoin"] = comparer_au_temoin(
            rendements, [g["rendement"] for g in grappes_t])
    return resume
