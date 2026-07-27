"""Modele de frais Polymarket, edition 2026.

La formule est en cloche :

    frais = nb_parts x taux x prix x (1 - prix)

Consequence majeure pour la strategie : les frais s'annulent aux extremes.
A 0,50 $ ils valent 1,0 % du montant engage en politique ; a 0,95 $ ils
tombent a 0,21 %. Une strategie qui travaille pres de 0 ou de 1 part donc
avec un avantage de cout structurel, et une strategie qui apporte de la
liquidite ne paie rien du tout.

Les taux sont figes ici volontairement plutot que lus a chaud : ils font
partie des hypotheses de l'experience et doivent etre datables. Toute
revision se fait par un commit, donc horodatee.
"""

from __future__ import annotations

# Taux par categorie, releves le 2026-07-27.
TAUX = {
    "politics": 0.04,
    "finance": 0.04,
    "tech": 0.04,
    "mentions": 0.04,
    "sports": 0.05,
    "economics": 0.05,
    "culture": 0.05,
    "weather": 0.05,
    "other": 0.05,
    "crypto": 0.07,
    "geopolitics": 0.0,
}

TAUX_PAR_DEFAUT = 0.05  # le plus penalisant hors crypto : on ne se flatte pas
TAUX_INCONNU_PRUDENT = 0.07  # si la categorie est illisible, on suppose le pire

# Correspondances des libelles rencontres sur Gamma vers nos categories.
ALIAS = {
    "politique": "politics", "us-politics": "politics", "elections": "politics",
    "election": "politics", "trump": "politics", "world": "geopolitics",
    "geopolitique": "geopolitics", "war": "geopolitics", "middle-east": "geopolitics",
    "ukraine": "geopolitics", "israel": "geopolitics",
    "sport": "sports", "nba": "sports", "nfl": "sports", "mlb": "sports",
    "nhl": "sports", "soccer": "sports", "football": "sports", "tennis": "sports",
    "ufc": "sports", "mma": "sports", "boxing": "sports", "f1": "sports",
    "bitcoin": "crypto", "ethereum": "crypto", "btc": "crypto", "eth": "crypto",
    "solana": "crypto", "crypto-prices": "crypto",
    "economy": "economics", "fed": "economics", "inflation": "economics",
    "cpi": "economics", "gdp": "economics", "rates": "economics",
    "business": "finance", "stocks": "finance", "earnings": "finance",
    "ai": "tech", "technology": "tech", "science": "tech", "space": "tech",
    "pop-culture": "culture", "entertainment": "culture", "movies": "culture",
    "music": "culture", "awards": "culture", "oscars": "culture",
    "temperature": "weather", "climate": "weather", "hurricane": "weather",
    "mention": "mentions", "will-x-say": "mentions",
}


def categoriser(etiquettes) -> str:
    """Deduit une categorie de frais a partir des etiquettes d'un evenement.

    En cas d'ambiguite on retient la categorie la plus chere : sous-estimer
    les frais reviendrait a s'inventer de l'alpha.
    """
    if not etiquettes:
        return "inconnu"
    if isinstance(etiquettes, str):
        etiquettes = [etiquettes]

    trouvees = []
    for brut in etiquettes:
        if isinstance(brut, dict):
            brut = brut.get("slug") or brut.get("label") or brut.get("name") or ""
        cle = str(brut).strip().lower().replace(" ", "-")
        if cle in TAUX:
            trouvees.append(cle)
        elif cle in ALIAS:
            trouvees.append(ALIAS[cle])

    if not trouvees:
        return "inconnu"
    # La plus chere l'emporte.
    return max(trouvees, key=lambda c: TAUX.get(c, TAUX_PAR_DEFAUT))


def taux_de(categorie) -> float:
    if categorie == "inconnu" or categorie is None:
        return TAUX_INCONNU_PRUDENT
    return TAUX.get(categorie, TAUX_PAR_DEFAUT)


def frais_preneur(nb_parts: float, prix: float, categorie: str) -> float:
    """Frais d'un ordre au marche. Toujours positif ou nul."""
    if nb_parts <= 0:
        return 0.0
    p = min(max(float(prix), 0.0), 1.0)
    return abs(nb_parts) * taux_de(categorie) * p * (1.0 - p)


def frais_apporteur(nb_parts: float, prix: float, categorie: str) -> float:
    """Un ordre a cours limite pose dans le carnet ne paie rien.

    La ristourne de 15 a 25 % sur les frais des preneurs existe mais n'est
    volontairement pas comptee : elle gonflerait le resultat d'un revenu que
    nous ne pouvons pas verifier depuis l'exterieur.
    """
    return 0.0


def frais_en_pourcentage(prix: float, categorie: str) -> float:
    """Frais rapportes au montant engage, utile pour filtrer un signal.

    A l'achat, engager nb_parts x prix dollars coute nb_parts x taux x p x (1-p),
    soit taux x (1 - p) du montant.
    """
    p = min(max(float(prix), 1e-9), 1.0 - 1e-9)
    return taux_de(categorie) * (1.0 - p)


def seuil_d_avantage(prix: float, categorie: str, marge: float = 1.5) -> float:
    """Avantage minimal, en points de probabilite, pour qu'un pari vaille la peine.

    Un aller-retour coute les frais deux fois dans le pire cas. La marge par
    defaut exige une fois et demie ce cout, pour ne pas jouer des signaux dont
    l'esperance est nulle une fois les couts payes.
    """
    p = min(max(float(prix), 1e-9), 1.0 - 1e-9)
    cout_unitaire = taux_de(categorie) * p * (1.0 - p)
    return marge * cout_unitaire
