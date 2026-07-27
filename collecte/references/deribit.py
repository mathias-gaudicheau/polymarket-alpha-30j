"""Probabilites implicites tirees du marche d'options Deribit.

Pourquoi cette source. Polymarket cote des marches du type "le bitcoin
depassera-t-il 120 000 dollars le 31 juillet". Deribit cote au meme instant
des options sur le meme sous-jacent, avec des echeances et des prix
d'exercice voisins, sur un carnet arbitre en permanence par des
professionnels. Un marche d'options ne donne pas un avis : il donne une
distribution de probabilite complete, tenue par des gens dont c'est le
metier. La confrontation est tres inegale.

Comment. Sous la mesure risque-neutre, la probabilite que le sous-jacent
finisse au-dessus d'un prix d'exercice vaut N(d2) de Black-Scholes. Deribit
publie la volatilite implicite de chaque option ; on interpole cette surface
au point voulu, puis on evalue N(d2).

Deux reserves, qui interdisent de traiter cette source comme un oracle.

La probabilite risque-neutre n'est pas la probabilite reelle : elle inclut
une prime de risque. Sur la crypto cet ecart est modeste mais pas nul, et il
penche systematiquement dans le meme sens. Ensuite, Polymarket et Deribit ne
se referent pas forcement au meme indice ni a la meme heure de reglement.

C'est pourquoi cette source recoit un poids de confiance mesure sur nos
propres denouements, et non une confiance de principe.
"""

from __future__ import annotations

import math
import re
import time

from outils.commun import http_json

DERIBIT = "https://www.deribit.com/api/v2/public"
DEVISES = ("BTC", "ETH")

# Une option sans cotation ni interet ouvert ne renseigne sur rien.
INTERET_MINIMAL = 1.0

_NOM = re.compile(r"^(?P<devise>[A-Z]+)-(?P<echeance>\d{1,2}[A-Z]{3}\d{2})"
                  r"-(?P<exercice>[\d.]+)-(?P<type>[CP])$")

_MOIS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
         "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


def loi_normale(x: float) -> float:
    """Fonction de repartition de la loi normale centree reduite."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _echeance_en_horodatage(texte: str) -> int | None:
    """Convertit 25JUL26 en secondes depuis l'epoque, reglement a 08h00 UTC."""
    m = re.match(r"^(\d{1,2})([A-Z]{3})(\d{2})$", texte)
    if not m:
        return None
    jour, mois, annee = int(m.group(1)), _MOIS.get(m.group(2)), 2000 + int(m.group(3))
    if not mois:
        return None
    try:
        return int(time.mktime((annee, mois, jour, 8, 0, 0, 0, 0, 0))
                   - time.timezone)
    except (ValueError, OverflowError):
        return None


def charger_surface(devise="BTC", journal=None):
    """Recupere toutes les options vivantes d'une devise et en fait une surface.

    Renvoie {"spot": float, "options": [...]} ou None si la source est muette.
    """
    rep = http_json(DERIBIT + "/get_book_summary_by_currency",
                    {"currency": devise, "kind": "option"}, essais=2, timeout=30)
    if not rep.ok or not isinstance(rep.donnees, dict):
        if journal:
            journal("deribit %s : source injoignable (%s)" % (devise, rep.resume()[:60]))
        return None

    brut = rep.donnees.get("result") or []
    options, spot = [], None
    for o in brut:
        nom = o.get("instrument_name") or ""
        m = _NOM.match(nom)
        if not m:
            continue
        iv = o.get("mark_iv")
        if iv is None or iv <= 0:
            continue
        if (o.get("open_interest") or 0) < INTERET_MINIMAL:
            continue
        echeance = _echeance_en_horodatage(m.group("echeance"))
        if echeance is None:
            continue
        sous_jacent = o.get("underlying_price")
        if sous_jacent:
            spot = sous_jacent
        options.append({
            "exercice": float(m.group("exercice")),
            "echeance": echeance,
            "type": m.group("type"),
            # Deribit exprime la volatilite implicite en pourcentage.
            "vol": float(iv) / 100.0,
            "sous_jacent": sous_jacent,
            "interet": o.get("open_interest") or 0,
        })

    if not options or not spot:
        if journal:
            journal("deribit %s : aucune option exploitable" % devise)
        return None

    if journal:
        echeances = sorted({o["echeance"] for o in options})
        journal("deribit %s : %d options, %d echeances, sous-jacent %.0f"
                % (devise, len(options), len(echeances), spot))
    return {"spot": spot, "options": options, "devise": devise, "t": int(time.time())}


def _vol_interpolee(surface, exercice, echeance):
    """Volatilite implicite au point demande.

    On interpole en variance totale le long du temps -- c'est la grandeur qui
    s'additionne -- et en logarithme de moneyness le long des prix
    d'exercice. Extrapoler au-dela de la nappe cotee serait inventer : on
    renvoie None plutot qu'un chiffre confortable.
    """
    options = surface["options"]
    echeances = sorted({o["echeance"] for o in options})
    if not echeances:
        return None
    if echeance < echeances[0] or echeance > echeances[-1]:
        # Une echeance hors de la nappe cotee n'est pas interpolable.
        return None

    avant = max((e for e in echeances if e <= echeance), default=None)
    apres = min((e for e in echeances if e >= echeance), default=None)
    if avant is None or apres is None:
        return None

    def vol_a_echeance(ech):
        lot = [o for o in options if o["echeance"] == ech]
        if not lot:
            return None
        # Sourire de volatilite : on interpole en log-moneyness.
        lot.sort(key=lambda o: o["exercice"])
        exercices = [o["exercice"] for o in lot]
        if exercice <= exercices[0]:
            return lot[0]["vol"]
        if exercice >= exercices[-1]:
            return lot[-1]["vol"]
        for i in range(len(lot) - 1):
            a, b = lot[i], lot[i + 1]
            if a["exercice"] <= exercice <= b["exercice"]:
                if b["exercice"] == a["exercice"]:
                    return a["vol"]
                poids = ((math.log(exercice) - math.log(a["exercice"]))
                         / (math.log(b["exercice"]) - math.log(a["exercice"])))
                return a["vol"] + poids * (b["vol"] - a["vol"])
        return lot[-1]["vol"]

    v_avant, v_apres = vol_a_echeance(avant), vol_a_echeance(apres)
    if v_avant is None or v_apres is None:
        return None
    if avant == apres:
        return v_avant

    maintenant = surface["t"]
    t_avant = max(1e-6, (avant - maintenant) / 31557600.0)
    t_apres = max(1e-6, (apres - maintenant) / 31557600.0)
    t_cible = max(1e-6, (echeance - maintenant) / 31557600.0)

    # Variance totale, seule grandeur qui s'additionne dans le temps.
    var_avant = v_avant ** 2 * t_avant
    var_apres = v_apres ** 2 * t_apres
    if t_apres == t_avant:
        var = var_avant
    else:
        poids = (t_cible - t_avant) / (t_apres - t_avant)
        var = var_avant + poids * (var_apres - var_avant)
    if var <= 0:
        return None
    return math.sqrt(var / t_cible)


def proba_au_dessus(surface, exercice, echeance, journal=None):
    """Probabilite risque-neutre que le sous-jacent finisse au-dessus.

    Renvoie (probabilite, detail) ou (None, motif) si le point n'est pas
    interpolable sans extrapoler.
    """
    if not surface:
        return None, "surface absente"
    maintenant = surface["t"]
    duree = (echeance - maintenant) / 31557600.0
    if duree <= 0:
        return None, "echeance passee"
    if duree > 2.0:
        return None, "echeance trop lointaine"

    vol = _vol_interpolee(surface, exercice, echeance)
    if vol is None or vol <= 0:
        return None, "hors de la nappe cotee"

    spot = surface["spot"]
    # Taux sans risque neglige : sur la crypto il est proche de zero et son
    # effet est d'un ordre inferieur a l'incertitude sur la volatilite.
    d2 = (math.log(spot / exercice) - 0.5 * vol * vol * duree) / (vol * math.sqrt(duree))
    proba = loi_normale(d2)
    return proba, {
        "sous_jacent": spot, "exercice": exercice, "vol": round(vol, 4),
        "duree_an": round(duree, 5), "d2": round(d2, 4),
        "mesure": "risque-neutre",
    }


def charger_toutes(journal=None):
    """Surfaces des devises suivies, indexees par symbole."""
    surfaces = {}
    for devise in DEVISES:
        s = charger_surface(devise, journal)
        if s:
            surfaces[devise] = s
    return surfaces
