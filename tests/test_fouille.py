"""Tests du moteur de decouverte.

Le test qui compte est celui du bruit. On soumet au moteur des donnees ou,
par construction, il n'y a strictement rien a trouver : les resultats sont
tires au hasard selon le prix affiche, donc le marche a exactement raison.
Un moteur qui remonte des motifs dans ces conditions est un moteur qui
ruinera le portefeuille, et il vaut mieux le decouvrir ici.

Le second test verifie l'inverse : un avantage franc et volontairement
introduit doit etre retrouve. Un detecteur qui ne trouve jamais rien serait
irreprochable et inutile.

Lance directement : python tests/test_fouille.py
"""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyse.metriques import benjamini_hochberg, valeur_p_binomiale  # noqa: E402
from decouverte import fouille  # noqa: E402
from outils.commun import force_utf8  # noqa: E402

echecs = []


def verifier(condition, libelle, detail=""):
    if condition:
        print("  OK    %s" % libelle)
    else:
        print("  ECHEC %s  %s" % (libelle, detail))
        echecs.append(libelle)


def bruit_pur(n=4000, graine=7):
    """Marche parfaitement efficient : le prix est la vraie probabilite.

    Les caracteristiques sont tirees independamment du resultat. Il n'y a
    donc rien a trouver, et tout ce qui serait trouve serait faux.
    """
    alea = random.Random(graine)
    obs = []
    for i in range(n):
        prix = alea.uniform(0.05, 0.95)
        obs.append({
            "t": i,
            "prix": prix,
            "realise": alea.random() < prix,
            "ecart_cotation": alea.uniform(0.001, 0.05),
            "jours_restants": alea.uniform(0.5, 60.0),
            "liquidite": alea.uniform(500, 50000),
            "categorie": alea.choice(["politics", "sports", "crypto", "tech"]),
        })
    return obs


def avec_avantage_reel(n=4000, graine=11):
    """Meme marche, mais les cotes extremes sont reellement surpayees.

    Sous 0,15, la frequence reelle vaut la moitie du prix affiche. C'est le
    biais des cotes extremes, introduit ici volontairement.
    """
    alea = random.Random(graine)
    obs = []
    for i in range(n):
        prix = alea.uniform(0.02, 0.95)
        vraie = prix * 0.5 if prix < 0.15 else prix
        obs.append({
            "t": i,
            "prix": prix,
            "realise": alea.random() < vraie,
            "ecart_cotation": alea.uniform(0.001, 0.05),
            "jours_restants": alea.uniform(0.5, 60.0),
            "liquidite": alea.uniform(500, 50000),
            "categorie": alea.choice(["politics", "sports", "crypto", "tech"]),
        })
    return obs


CARACTERISTIQUES = ["prix", "ecart_cotation", "jours_restants", "liquidite",
                    "categorie"]


def test_le_bruit_ne_donne_rien():
    print("\n[le moteur ne doit rien trouver dans du bruit pur]")
    total = 0
    for graine in (7, 21, 42, 101, 314):
        obs = bruit_pur(graine=graine)
        motifs, diag = fouille.fouiller(obs, CARACTERISTIQUES)
        total += len(motifs)
        if motifs:
            print("      graine %d -> %d motif(s) : %s"
                  % (graine, len(motifs), [m["libelle"] for m in motifs][:3]))
    verifier(total == 0,
             "aucun motif sur cinq jeux de bruit pur",
             "%d motifs trouves au total" % total)


def test_un_vrai_avantage_est_retrouve():
    print("\n[un avantage franc doit etre retrouve]")
    motifs, diag = fouille.fouiller(avec_avantage_reel(), CARACTERISTIQUES)
    verifier(len(motifs) >= 1,
             "le biais des cotes extremes est detecte",
             "%d motifs, diagnostic %s" % (len(motifs), diag))
    if motifs:
        sur_prix = [m for m in motifs if m["caracteristique"] == "prix"]
        verifier(bool(sur_prix),
                 "le motif porte bien sur la tranche de prix",
                 str([m["caracteristique"] for m in motifs]))
        if sur_prix:
            m = sur_prix[0]
            verifier(m["sens"] == "sur_cote",
                     "le sens est correct : ces marches sont sur-cotes",
                     m["sens"])
            print("      %s" % fouille.redaction_declaration(m)[:200])


def test_effectif_insuffisant():
    print("\n[un effectif trop faible ne produit rien]")
    motifs, diag = fouille.fouiller(bruit_pur(n=50), CARACTERISTIQUES)
    verifier(motifs == [],
             "cinquante observations ne suffisent a rien conclure")


def test_correction_tests_multiples():
    print("\n[la correction de Benjamini-Hochberg]")
    # Cent valeurs p uniformes : aucune vraie decouverte, on en tolere peu.
    alea = random.Random(3)
    uniformes = [alea.random() for _ in range(100)]
    retenus = benjamini_hochberg(uniformes, 0.10)
    verifier(len(retenus) <= 2,
             "presque rien n'est retenu sur des valeurs p uniformes",
             "%d retenus" % len(retenus))

    # Dix effets francs noyes dans quatre-vingt-dix non-effets.
    melange = [1e-6] * 10 + [alea.random() for _ in range(90)]
    retenus = benjamini_hochberg(melange, 0.10)
    verifier(len(retenus) >= 10,
             "les dix vrais effets sont retrouves",
             "%d retenus" % len(retenus))


def test_valeur_p_binomiale():
    print("\n[la valeur p binomiale]")
    # Cinquante succes sur cent a probabilite un demi : parfaitement banal.
    verifier(0.4 < valeur_p_binomiale(50, 100, 0.5) < 0.6,
             "cinquante sur cent a une chance sur deux n'a rien de notable",
             "%.4f" % valeur_p_binomiale(50, 100, 0.5))
    # Quatre-vingts succes sur cent : tres improbable par hasard.
    verifier(valeur_p_binomiale(80, 100, 0.5) < 1e-8,
             "quatre-vingts sur cent est statistiquement extraordinaire",
             "%.3g" % valeur_p_binomiale(80, 100, 0.5))


def main():
    force_utf8()
    print("=" * 66)
    print("TESTS DU MOTEUR DE DECOUVERTE")
    print("=" * 66)
    test_valeur_p_binomiale()
    test_correction_tests_multiples()
    test_effectif_insuffisant()
    test_le_bruit_ne_donne_rien()
    test_un_vrai_avantage_est_retrouve()

    print("\n" + "=" * 66)
    if echecs:
        print("%d ECHEC(S) : %s" % (len(echecs), " | ".join(echecs)))
        return 1
    print("Tout passe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
