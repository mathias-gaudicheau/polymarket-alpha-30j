"""Tests des detecteurs, avec les cas qui m'ont deja pris en defaut.

Chaque cas ici correspond a une erreur reellement commise et corrigee. Les
garder sous forme de test est la seule facon de ne pas les recommettre.

Lance directement : python tests/test_detecteurs.py
Codes de sortie : 0 tout passe, 1 au moins un echec.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detecteurs import incoherences as inc  # noqa: E402
from detecteurs.socle import Jambe, Signal  # noqa: E402
from moteur import frais as mf  # noqa: E402
from moteur.execution import Carnet, executer_preneur, taille_kelly  # noqa: E402
from outils.commun import force_utf8  # noqa: E402

echecs = []


def verifier(condition, libelle, detail=""):
    if condition:
        print("  OK    %s" % libelle)
    else:
        print("  ECHEC %s  %s" % (libelle, detail))
        echecs.append(libelle)


def marche(id_, titre, achat, vente, seuil=None, evenement="ev1",
           categorie="politics", taux=0.04):
    return {
        "id": str(id_), "groupe_titre": titre, "question": titre,
        "achat": achat, "vente": vente, "groupe_seuil": seuil,
        "evenement_id": evenement, "accepte_ordres": True,
        "jeton_oui": "oui%s" % id_, "jeton_non": "non%s" % id_,
        "categorie": categorie, "taux_frais": taux,
    }


# --------------------------------------------------------------------------

def test_classement_des_groupes():
    print("\n[classement echelle contre partition]")

    # Le cas qui a produit treize faux signaux : des tranches disjointes
    # lues a tort comme une echelle emboitee.
    tranches = [marche(1, "1T-1.25T", 0.07, 0.08, 1.0),
                marche(2, "1.25T-1.5T", 0.03, 0.04, 1.25),
                marche(3, "1.5T-2T", 0.02, 0.03, 1.5)]
    verifier(inc.classer_groupe(tranches) == "partition",
             "des tranches sont reconnues comme partition",
             inc.classer_groupe(tranches))

    # Le piege exact : une seule tranche libellee avec un signe superieur
    # suffisait a faire classer tout le groupe en cumulatif.
    mixte = [marche(1, "25k-100k", 0.01, 0.02, 25),
             marche(2, ">100k", 0.05, 0.06, 100)]
    verifier(inc.classer_groupe(mixte) == "partition",
             "un groupe melant tranche et borne reste une partition",
             inc.classer_groupe(mixte))

    cumulatif = [marche(1, "above 100", 0.60, 0.62, 100),
                 marche(2, "above 110", 0.40, 0.42, 110),
                 marche(3, "above 120", 0.20, 0.22, 120)]
    verifier(inc.classer_groupe(cumulatif) == "cumulatif_au_dessus",
             "une vraie echelle cumulative est reconnue",
             inc.classer_groupe(cumulatif))

    verifier(inc.classer_groupe([marche(1, "Trump", 0.5, 0.52),
                                 marche(2, "Biden", 0.3, 0.32)]) == "inconnu",
             "des libelles sans structure restent inconnus")


def test_echelle_ne_signale_pas_les_partitions():
    print("\n[le detecteur d'echelle ignore les partitions]")
    tranches = [marche(1, "1T-1.25T", 0.07, 0.08, 1.0),
                marche(2, "1.25T-1.5T", 0.03, 0.04, 1.25)]
    verifier(inc.echelles_de_seuils(tranches) == [],
             "aucun signal sur une partition en tranches")

    # Vraie violation sur une vraie echelle, d'ampleur credible : doit passer.
    viole = [marche(1, "above 100", 0.30, 0.32, 100),
             marche(2, "above 110", 0.36, 0.38, 110)]
    signaux = inc.echelles_de_seuils(viole)
    verifier(len(signaux) == 1,
             "une monotonie reellement violee est detectee",
             "%d signaux" % len(signaux))

    # Violation trop belle pour etre vraie : consignee, pas jouee.
    enorme = [marche(1, "above 100", 0.30, 0.32, 100),
              marche(2, "above 110", 0.50, 0.52, 110)]
    verifier(inc.echelles_de_seuils(enorme) == [],
             "une violation de vingt points est jugee invraisemblable")

    # Echelle saine : rien a signaler.
    saine = [marche(1, "above 100", 0.60, 0.62, 100),
             marche(2, "above 110", 0.40, 0.42, 110)]
    verifier(inc.echelles_de_seuils(saine) == [],
             "une echelle monotone ne produit aucun signal")


def test_panier_exige_exhaustivite():
    print("\n[le panier exige un evenement complet]")

    # Le cas qui annoncait 17 % de rendement garanti : deux outsiders dont la
    # somme vaut 0,008, presentes comme un panier sous-cote.
    partiel = {"id": "e1", "slug": "ethiopie", "neg_risk": True, "complet": True,
               "marches": [marche(1, "A", 0.002, 0.003), marche(2, "B", 0.004, 0.005)]}
    signaux, ecartes = inc.paniers_negrisk([partiel])
    verifier(signaux == [],
             "deux outsiders ne forment pas un panier",
             "%d signaux" % len(signaux))
    verifier(ecartes["somme_implausible"] == 1,
             "le motif d'ecart est bien la somme implausible", str(ecartes))

    # Evenement incomplet : une issue a disparu au filtrage.
    incomplet = {"id": "e2", "slug": "x", "neg_risk": True, "complet": False,
                 "marches": [marche(1, "A", 0.40, 0.42), marche(2, "B", 0.50, 0.52)]}
    signaux, ecartes = inc.paniers_negrisk([incomplet])
    verifier(signaux == [] and ecartes["incomplet"] == 1,
             "un evenement incomplet est ecarte")

    # Vrai panier sous-cote, credible : somme des ventes a 0,975.
    vrai = {"id": "e3", "slug": "y", "neg_risk": True, "complet": True,
            "marches": [marche(1, "A", 0.47, 0.475), marche(2, "B", 0.49, 0.50)]}
    signaux, _ = inc.paniers_negrisk([vrai])
    verifier(len(signaux) == 1 and signaux[0].strategie == "c1_panier_sous_cote",
             "un vrai panier sous-cote est detecte",
             "%d signaux" % len(signaux))

    # Propriete structurelle, decouverte en ecrivant ces tests : le prix de
    # vente depasse toujours le prix moyen, donc la somme des ventes depasse
    # celle des milieux. Exiger que les milieux somment a moins de six points
    # de l'unite borne donc mecaniquement le gain d'un panier sous ce meme
    # seuil. Les deux garde-fous se recouvrent : aucun panier ne peut afficher
    # un gain incroyable sans avoir deja ete ecarte pour somme implausible.
    # C'est verifie ici plutot que suppose.
    trop_beau = {"id": "e4", "slug": "z", "neg_risk": True, "complet": True,
                 "marches": [marche(1, "A", 0.40, 0.41), marche(2, "B", 0.50, 0.51)]}
    signaux, ecartes = inc.paniers_negrisk([trop_beau])
    verifier(signaux == [] and ecartes["somme_implausible"] == 1,
             "un panier trop genereux tombe deja sur la borne de partition",
             str(ecartes))
    verifier(inc.TOLERANCE_PARTITION <= inc.PLAFOND_GAIN_CREDIBLE,
             "la tolerance de partition borne bien le gain atteignable",
             "%.3f contre %.3f" % (inc.TOLERANCE_PARTITION,
                                   inc.PLAFOND_GAIN_CREDIBLE))


def test_execution_en_parts():
    print("\n[l'execution se dimensionne en parts, pas en dollars]")
    carnet = Carnet("j", achats=[(0.30, 100.0)], ventes=[(0.32, 50.0), (0.34, 200.0)])

    r = executer_preneur(carnet, "achat", parts_visees=50, taux_frais=0.04)
    verifier(abs(r.parts - 50) < 1e-6 and abs(r.prix_moyen - 0.32) < 1e-9,
             "cinquante parts au premier niveau", repr(r))

    # Traversee de deux niveaux : le prix moyen doit se degrader.
    r2 = executer_preneur(carnet, "achat", parts_visees=150, taux_frais=0.04)
    attendu = (50 * 0.32 + 100 * 0.34) / 150
    verifier(abs(r2.prix_moyen - attendu) < 1e-9,
             "le prix moyen se degrade en traversant le carnet",
             "%.5f contre %.5f" % (r2.prix_moyen, attendu))

    # Jamais au prix median.
    verifier(r2.prix_moyen > carnet.milieu,
             "un achat ne se remplit jamais au prix median")

    # Profondeur insuffisante : borne, pas invention.
    r3 = executer_preneur(carnet, "achat", parts_visees=5000, taux_frais=0.04)
    verifier(r3.parts <= 250.0 and r3.borne_par_profondeur,
             "la taille est bornee par la profondeur reelle", repr(r3))

    # La jambe la plus etroite dicte la taille.
    verifier(abs(carnet.parts_disponibles("achat", 0.33) - 50.0) < 1e-9,
             "la profondeur sous un prix limite est correctement mesuree")


def test_frais_en_cloche():
    print("\n[les frais suivent bien une cloche]")
    f_milieu = mf.frais_preneur(100, 0.50, "politics")
    f_bord = mf.frais_preneur(100, 0.95, "politics")
    verifier(abs(f_milieu - 1.00) < 1e-9,
             "cent parts a 0,50 coutent un dollar en politique",
             "%.4f" % f_milieu)
    verifier(f_bord < f_milieu / 4,
             "les frais s'effondrent pres des extremes", "%.4f" % f_bord)
    verifier(mf.frais_preneur(100, 0.5, "geopolitics") == 0.0,
             "la geopolitique est exempte de frais")
    verifier(mf.frais_apporteur(100, 0.5, "crypto") == 0.0,
             "un apporteur de liquidite ne paie rien")
    # Le taux inconnu doit etre le plus penalisant, jamais le plus flatteur.
    verifier(mf.taux_de("inconnu") >= max(mf.TAUX.values()) - 1e-9,
             "une categorie inconnue est facturee au taux le plus cher")


def test_kelly_borne():
    print("\n[le dimensionnement reste borne]")
    verifier(taille_kelly(0.10, 0.50, 500) <= 500 * 0.02 + 1e-9,
             "le plafond de deux pour cent prime sur la formule")
    verifier(taille_kelly(-0.05, 0.50, 500) == 0.0,
             "un avantage negatif ne produit aucune mise")
    verifier(taille_kelly(0.0, 0.50, 500) == 0.0,
             "un avantage nul ne produit aucune mise")


def main():
    force_utf8()
    print("=" * 66)
    print("TESTS DES DETECTEURS ET DU MOTEUR")
    print("=" * 66)
    test_classement_des_groupes()
    test_echelle_ne_signale_pas_les_partitions()
    test_panier_exige_exhaustivite()
    test_execution_en_parts()
    test_frais_en_cloche()
    test_kelly_borne()

    print("\n" + "=" * 66)
    if echecs:
        print("%d ECHEC(S) : %s" % (len(echecs), " | ".join(echecs)))
        return 1
    print("Tout passe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
