"""Tests des garde-fous : coupe-circuit, temoin, comptage en grappes.

Un coupe-circuit qu'on n'a jamais vu se declencher n'est pas un
coupe-circuit, c'est une intention. Ces tests le forcent.

Lance directement : python tests/test_garde_fous.py
"""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyse import metriques, quotidien  # noqa: E402
from outils.commun import force_utf8  # noqa: E402

echecs = []


def verifier(condition, libelle, detail=""):
    if condition:
        print("  OK    %s" % libelle)
    else:
        print("  ECHEC %s  %s" % (libelle, detail))
        echecs.append(libelle)


def test_coupe_circuit():
    print("\n[le coupe-circuit]")
    capital = quotidien.CAPITAL_INITIAL

    calmes = quotidien.evaluer_alertes(-10.0, 100, [])
    verifier(not any(a["gravite"] == "CRITIQUE" for a in calmes),
             "une petite perte ne declenche rien")

    limite = quotidien.evaluer_alertes(-capital * 0.19, 100, [])
    verifier(not any(a["gravite"] == "CRITIQUE" for a in limite),
             "dix-neuf pour cent de perte ne declenche pas encore")

    franchi = quotidien.evaluer_alertes(-capital * 0.21, 100, [])
    verifier(any(a["gravite"] == "CRITIQUE" for a in franchi),
             "vingt-et-un pour cent de perte declenche le coupe-circuit",
             str(franchi))

    exact = quotidien.evaluer_alertes(-capital * 0.20, 100, [])
    verifier(any(a["gravite"] == "CRITIQUE" for a in exact),
             "le seuil exact declenche aussi, sans zone grise")

    verifier(any(a["gravite"] == "ALERTE" for a in
                 quotidien.evaluer_alertes(-capital * 0.11, 100, [])),
             "une alerte precede le coupe-circuit a mi-chemin")


def test_collecte_muette():
    print("\n[la collecte muette]")
    verifier(any("collecte" in a["sujet"] for a in
                 quotidien.evaluer_alertes(0.0, 0, [])),
             "aucune execution consignee leve une alerte")


def test_latence_excessive():
    print("\n[la latence excessive]")
    lentes = [{"latence": 1800} for _ in range(9)]
    verifier(any("latence" in a["sujet"] for a in
                 quotidien.evaluer_alertes(0.0, 10, lentes)),
             "une latence mediane d'une demi-heure leve une alerte")
    rapides = [{"latence": 300} for _ in range(9)]
    verifier(not any("latence" in a["sujet"] for a in
                     quotidien.evaluer_alertes(0.0, 10, rapides)),
             "cinq minutes de latence ne leve rien")


def test_comptage_en_grappes():
    print("\n[le comptage en grappes]")
    # Quinze positions sur une meme echelle bitcoin : une seule observation.
    positions = [{"grappe": "btc", "mise": 10.0, "gain": 1.0} for _ in range(15)]
    grappes = metriques.rendement_par_grappe(positions)
    verifier(len(grappes) == 1,
             "quinze positions liees ne font qu'une grappe",
             "%d grappes" % len(grappes))

    melange = positions + [{"grappe": "eth", "mise": 10.0, "gain": -2.0}
                           for _ in range(8)]
    verifier(len(metriques.rendement_par_grappe(melange)) == 2,
             "deux echelles distinctes font deux grappes")

    # La consequence chiffree : l'intervalle ne doit pas retrecir avec le
    # nombre de paris, seulement avec le nombre de grappes.
    beaucoup = []
    for g in range(4):
        beaucoup += [{"grappe": "g%d" % g, "mise": 10.0, "gain": 1.0}
                     for _ in range(25)]
    resume = metriques.resume_strategie(beaucoup, nom="essai")
    verifier(resume["n_paris"] == 100 and resume["n_grappes"] == 4,
             "cent paris repartis en quatre grappes sont comptes comme quatre",
             "%d paris / %d grappes" % (resume["n_paris"], resume["n_grappes"]))
    verifier(not resume["conclusion_permise"],
             "quatre grappes ne permettent aucune conclusion, malgre cent paris")


def test_temoin_aleatoire_ne_rapporte_rien():
    print("\n[le temoin aleatoire doit rapporter zero]")
    # Marche efficient : chaque pari gagne avec la probabilite de son prix.
    # Un temoin tire au hasard doit donc voir son rendement osciller autour
    # de zero, frais mis a part. Si ce test echouait, notre comptabilite
    # fabriquerait du gain a partir de rien.
    alea = random.Random(5)
    positions = []
    for i in range(600):
        prix = alea.uniform(0.1, 0.9)
        parts = 20.0
        mise = parts * prix
        gagne = alea.random() < prix
        positions.append({"grappe": "t%d" % i, "mise": mise,
                          "gain": (parts if gagne else 0.0) - mise})
    resume = metriques.resume_strategie(positions, nom="temoin")
    ic = resume["intervalle"]
    verifier(ic is not None and ic["bas"] < 0 < ic["haut"],
             "l'intervalle du temoin contient bien zero",
             "de %.4f a %.4f" % (ic["bas"], ic["haut"]) if ic else "aucun")
    verifier(abs(resume["rendement_global"]) < 0.06,
             "le rendement global du temoin reste proche de zero",
             "%.4f" % resume["rendement_global"])


def test_comparaison_au_temoin():
    print("\n[la comparaison au temoin]")
    identiques = [0.01, -0.02, 0.03, -0.01, 0.02] * 8
    c = metriques.comparer_au_temoin(identiques, list(identiques))
    verifier(not c["significatif"],
             "deux series identiques ne montrent aucun avantage")

    meilleur = [x + 0.20 for x in identiques]
    c2 = metriques.comparer_au_temoin(meilleur, identiques)
    verifier(c2["significatif"],
             "un avantage franc de vingt points ressort comme significatif",
             str(c2))


def main():
    force_utf8()
    print("=" * 66)
    print("TESTS DES GARDE-FOUS")
    print("=" * 66)
    test_coupe_circuit()
    test_collecte_muette()
    test_latence_excessive()
    test_comptage_en_grappes()
    test_temoin_aleatoire_ne_rapporte_rien()
    test_comparaison_au_temoin()

    print("\n" + "=" * 66)
    if echecs:
        print("%d ECHEC(S) : %s" % (len(echecs), " | ".join(echecs)))
        return 1
    print("Tout passe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
