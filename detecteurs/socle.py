"""Socle commun aux detecteurs.

Un detecteur observe l'univers a un instant donne et renvoie des signaux.
Un signal decrit une intention datee, pas une execution : le moteur decidera
ensuite s'il est realisable, a quelle taille, et a quel prix reel.

Un signal peut porter plusieurs jambes. Acheter le panier complet d'un
evenement a six issues, c'est six jambes qui n'ont de sens qu'ensemble : soit
toutes s'executent, soit le signal est abandonne. Cette notion de signal
tout-ou-rien est indispensable, sinon une jambe non remplie transforme un
arbitrage sans risque en pari directionnel.
"""

from __future__ import annotations

from moteur import frais as mf

# Un avantage plus petit que cela n'est que du bruit de tick.
AVANTAGE_MINIMAL = 0.005


class Jambe:
    """Une prise de position elementaire sur un jeton."""

    __slots__ = ("marche_id", "jeton", "sens", "prix", "categorie", "taux_frais",
                 "libelle")

    def __init__(self, marche_id, jeton, sens, prix, categorie="inconnu",
                 taux_frais=None, libelle=None):
        self.marche_id = marche_id
        self.jeton = jeton
        self.sens = sens          # 'achat' ou 'vente'
        self.prix = float(prix)
        self.categorie = categorie
        self.taux_frais = taux_frais if taux_frais is not None else mf.taux_de(categorie)
        self.libelle = libelle

    def cout_frais(self, nb_parts):
        return abs(nb_parts) * self.taux_frais * self.prix * (1.0 - self.prix)

    def en_dict(self):
        return {"marche_id": self.marche_id, "jeton": self.jeton, "sens": self.sens,
                "prix": self.prix, "categorie": self.categorie,
                "taux_frais": self.taux_frais, "libelle": self.libelle}


class Signal:
    """Une intention datee, avec sa justification chiffree."""

    __slots__ = ("strategie", "jambes", "avantage", "explication", "tout_ou_rien",
                 "gain_certain", "reference", "proba_estimee", "prix_reference",
                 "marche_pivot")

    def __init__(self, strategie, jambes, avantage, explication,
                 tout_ou_rien=False, gain_certain=None, reference=None,
                 proba_estimee=None, prix_reference=None, marche_pivot=None):
        self.strategie = strategie
        self.jambes = jambes
        self.avantage = float(avantage)
        self.explication = explication
        self.tout_ou_rien = tout_ou_rien
        # Pour un arbitrage : gain garanti par dollar engage, hors frais.
        self.gain_certain = gain_certain
        self.reference = reference          # d'ou vient la probabilite estimee
        self.proba_estimee = proba_estimee
        self.prix_reference = prix_reference
        self.marche_pivot = marche_pivot or (jambes[0].marche_id if jambes else None)

    def frais_totaux(self, nb_parts_par_jambe):
        return sum(j.cout_frais(nb_parts_par_jambe) for j in self.jambes)

    def avantage_net(self, nb_parts=100.0):
        """Avantage une fois les frais de toutes les jambes payes."""
        engage = sum(j.prix * nb_parts for j in self.jambes if j.sens == "achat")
        if engage <= 0:
            engage = nb_parts
        return self.avantage - (self.frais_totaux(nb_parts) / engage)

    def en_dict(self):
        return {
            "strategie": self.strategie,
            "avantage": self.avantage,
            "avantage_net": self.avantage_net(),
            "explication": self.explication,
            "tout_ou_rien": self.tout_ou_rien,
            "gain_certain": self.gain_certain,
            "reference": self.reference,
            "proba_estimee": self.proba_estimee,
            "prix_reference": self.prix_reference,
            "marche_pivot": self.marche_pivot,
            "jambes": [j.en_dict() for j in self.jambes],
        }

    def __repr__(self):
        return "<Signal %s avantage=%.4f net=%.4f %d jambe(s)>" % (
            self.strategie, self.avantage, self.avantage_net(), len(self.jambes))


def prix_negociables(marche) -> bool:
    """Un marche n'est exploitable que s'il a deux cotations coherentes."""
    a, v = marche.get("achat"), marche.get("vente")
    if a is None or v is None:
        return False
    if not (0.0 < a < 1.0 and 0.0 < v < 1.0):
        return False
    if v < a:                      # carnet croise : donnee suspecte, on s'abstient
        return False
    if (v - a) > 0.20:             # ecart si large qu'aucun prix n'est significatif
        return False
    return bool(marche.get("accepte_ordres"))


def filtrer_signaux(signaux, avantage_minimal=AVANTAGE_MINIMAL):
    """Ne garde que les signaux dont l'avantage survit aux frais."""
    gardes = []
    for s in signaux:
        if s.avantage < avantage_minimal:
            continue
        if s.avantage_net() <= 0:
            continue
        gardes.append(s)
    return gardes
