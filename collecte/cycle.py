"""Cycle de collecte, execute toutes les cinq minutes.

Enchainement d'un cycle :

  1. lire l'univers complet chez Gamma (une vingtaine de requetes)
  2. executer les signaux emis au cycle precedent, contre les carnets reels
     d'aujourd'hui  -- c'est la latence de cinq minutes, imposee et non subie
  3. faire tourner les detecteurs sur l'instantane du jour
  4. deposer les nouveaux signaux en attente, chacun double d'un temoin
     aleatoire tire dans le meme univers
  5. ecrire le journal et rendre compte

Aucun acces a la base : le quota de calcul gratuit ne le supporterait pas a
cette cadence. La base est reveillee par le travail quotidien.

Codes de sortie : 0 cycle sain, 1 anomalie signalee, 2 echec de collecte.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collecte import gamma, journal  # noqa: E402
from detecteurs import incoherences  # noqa: E402
from moteur.execution import Carnet, executer_preneur  # noqa: E402
from outils.commun import force_utf8, http_json, maintenant_iso  # noqa: E402

CLOB = "https://clob.polymarket.com"
LOT_CARNETS = 200          # mesure : /books accepte 200 jetons par appel
FICHIER_ATTENTE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "journal", "en_attente.json")

# Budget de reference d'un signal, toutes jambes confondues. Le portefeuille
# reel affinera au travail quotidien ; ici on mesure surtout la faisabilite.
BUDGET_ESSAI = 20.0


def parts_pour_budget(jambes, budget=BUDGET_ESSAI):
    """Combien de parts acheter sur chaque jambe pour respecter le budget.

    Un arbitrage se dimensionne en parts identiques sur toutes les jambes,
    jamais en montants identiques : c'est le nombre de parts qui se compense
    au denouement. Le cout d'un lot complet vaut la somme des prix ; le budget
    divise par cette somme donne le nombre de lots finançables.
    """
    cout_du_lot = sum(j["prix"] for j in jambes if j["sens"] == "achat")
    if cout_du_lot <= 0:
        return 0.0
    return budget / cout_du_lot


def note(message):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), message))


# --------------------------------------------------------------------------
# Carnets profonds, uniquement pour les marches concernes par un signal
# --------------------------------------------------------------------------

def charger_carnets(jetons):
    """Recupere les carnets complets par lots de 200."""
    carnets, echecs = {}, 0
    uniques = list(dict.fromkeys(j for j in jetons if j))
    for depart in range(0, len(uniques), LOT_CARNETS):
        lot = uniques[depart:depart + LOT_CARNETS]
        rep = http_json(CLOB + "/books",
                        corps=[{"token_id": j} for j in lot],
                        methode="POST", essais=3, timeout=40)
        if not rep.ok or not isinstance(rep.donnees, list):
            echecs += len(lot)
            continue
        for brut in rep.donnees:
            jeton = brut.get("asset_id") or brut.get("token_id")
            if jeton:
                carnets[jeton] = Carnet.depuis_api(brut, jeton)
    return carnets, echecs


# --------------------------------------------------------------------------
# Execution des signaux du cycle precedent
# --------------------------------------------------------------------------

def lire_attente():
    if not os.path.exists(FICHIER_ATTENTE):
        return []
    try:
        with open(FICHIER_ATTENTE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def ecrire_attente(signaux):
    os.makedirs(os.path.dirname(FICHIER_ATTENTE), exist_ok=True)
    with open(FICHIER_ATTENTE, "w", encoding="utf-8") as f:
        json.dump(signaux, f, ensure_ascii=False, separators=(",", ":"))


def executer_en_attente(attente, par_id):
    """Confronte chaque signal en attente aux carnets d'aujourd'hui.

    Un signal tout-ou-rien n'est retenu que si toutes ses jambes se
    remplissent. Sinon l'arbitrage se transformerait en pari directionnel,
    ce qui reviendrait a s'attribuer un avantage qui n'existe plus.
    """
    if not attente:
        return [], {"nb": 0}

    jetons = [j["jeton"] for s in attente for j in s.get("jambes", [])]
    carnets, echecs_carnets = charger_carnets(jetons)
    note("carnets profonds : %d recuperes, %d en echec" % (len(carnets), echecs_carnets))

    executions = []
    for s in attente:
        resultat = {
            "strategie": s["strategie"],
            "t_signal": s.get("t"),
            "t_execution": int(time.time()),
            "temoin": s.get("temoin", False),
            "avantage_annonce": s.get("avantage"),
            "marche_pivot": s.get("marche_pivot"),
            "jambes": [],
        }
        toutes_remplies = True
        net_total, frais_total = 0.0, 0.0

        for jambe in s.get("jambes", []):
            carnet = carnets.get(jambe["jeton"])
            if carnet is None or not carnet.est_exploitable():
                toutes_remplies = False
                resultat["jambes"].append({
                    "jeton": jambe["jeton"], "rempli": False,
                    "motif": "carnet absent ou inexploitable"})
                continue

            # Toutes les jambes visent le meme nombre de parts : c'est ce qui
            # fait qu'un panier se compense au denouement.
            parts_visees = s.get("parts_visees") or parts_pour_budget(s["jambes"])
            # Refus d'aller chercher un prix nettement pire que celui vu :
            # au-dela, l'occasion a disparu et la poursuivre serait la subir.
            limite = (jambe["prix"] * 1.03 if jambe["sens"] == "achat"
                      else jambe["prix"] * 0.97)
            rem = executer_preneur(carnet, jambe["sens"],
                                   categorie=jambe.get("categorie", "inconnu"),
                                   prix_maximal=limite,
                                   parts_visees=parts_visees,
                                   taux_frais=jambe.get("taux_frais"))
            if not rem.rempli:
                toutes_remplies = False
            else:
                net_total += rem.montant_net
                frais_total += rem.frais
            resultat["jambes"].append({
                "jeton": jambe["jeton"],
                "rempli": rem.rempli,
                "parts": round(rem.parts, 3),
                "prix_vu": jambe["prix"],
                "prix_obtenu": round(rem.prix_moyen, 5) if rem.rempli else None,
                "glissement": (round(rem.prix_moyen - jambe["prix"], 5)
                               if rem.rempli else None),
                "frais": round(rem.frais, 5),
                "borne": rem.borne_par_profondeur,
                "motif": rem.motif,
            })

        resultat["realisee"] = toutes_remplies if s.get("tout_ou_rien") \
            else any(j["rempli"] for j in resultat["jambes"])
        resultat["montant_net"] = round(net_total, 5)
        resultat["frais"] = round(frais_total, 5)
        if not resultat["realisee"]:
            resultat["motif_echec"] = "jambe(s) non remplie(s)"

        # Pour un panier exhaustif, exactement une jambe vaut 1 $ au
        # denouement. Le gain se calcule donc sans rien prevoir : c'est le
        # nombre de parts du lot, moins ce qu'il a coute.
        if s.get("tout_ou_rien") and resultat["realisee"]:
            remplies = [j for j in resultat["jambes"] if j["rempli"]]
            if remplies:
                lot = min(j["parts"] for j in remplies)
                depense = -net_total          # net_total est negatif a l'achat
                resultat["gain_certain"] = round(lot - depense, 4)
                resultat["rendement"] = (round((lot - depense) / depense, 5)
                                         if depense > 0 else None)
                resultat["parts_du_lot"] = round(lot, 2)
                resultat["desequilibre_parts"] = round(
                    max(j["parts"] for j in remplies) - lot, 2)
        executions.append(resultat)

    realisees = sum(1 for e in executions if e["realisee"])
    return executions, {"nb": len(executions), "realisees": realisees,
                        "echecs_carnets": echecs_carnets}


# --------------------------------------------------------------------------
# Temoin aleatoire
# --------------------------------------------------------------------------

def fabriquer_temoin(signal_dict, univers, tirage):
    """Double un signal reel par un pari tire au hasard, meme instant, meme taille.

    Sans ce temoin, une serie de gains ne prouve rien : elle peut n'etre que
    la derive generale du marche sur la periode. Le temoin absorbe cette
    derive, et seul l'ecart entre les deux mesure un avantage.
    """
    candidats = [m for m in univers
                 if m.get("accepte_ordres") and m.get("achat") and m.get("vente")
                 and 0.05 < m["vente"] < 0.95]
    if not candidats:
        return None
    nb_jambes = max(1, len(signal_dict.get("jambes", [])))
    choisis = tirage.sample(candidats, min(nb_jambes, len(candidats)))
    return {
        "strategie": "temoin_aleatoire",
        "t": signal_dict.get("t"),
        "temoin": True,
        "jumeau_de": signal_dict.get("strategie"),
        "avantage": 0.0,
        "tout_ou_rien": False,
        "marche_pivot": choisis[0]["id"],
        "parts_visees": signal_dict.get("parts_visees"),
        "explication": "Pari temoin tire au hasard, jumeau d'un signal %s."
                       % signal_dict.get("strategie"),
        "jambes": [{
            "marche_id": m["id"], "jeton": m["jeton_oui"], "sens": "achat",
            "prix": m["vente"], "categorie": m["categorie"],
            "taux_frais": m["taux_frais"], "libelle": "temoin",
        } for m in choisis],
    }


# --------------------------------------------------------------------------
# Cycle complet
# --------------------------------------------------------------------------

def main():
    force_utf8()
    depart = time.time()
    horodatage = int(depart)
    print("=" * 70)
    print("CYCLE DE COLLECTE - %s" % maintenant_iso())
    print("=" * 70)

    # 1. Univers
    marches, evenements, diag_univers = gamma.lire_univers(journal=note)
    if not marches:
        print("ECHEC : univers vide, rien a faire. %s" % diag_univers)
        return 2
    par_id = {m["id"]: m for m in marches}

    # 2. Signaux du cycle precedent, executes contre les carnets d'aujourd'hui
    attente = lire_attente()
    note("signaux en attente du cycle precedent : %d" % len(attente))
    executions, diag_exec = executer_en_attente(attente, par_id)
    if executions:
        chemin = os.path.join(
            os.path.dirname(FICHIER_ATTENTE),
            time.strftime("%Y-%m-%d", time.gmtime(horodatage)), "executions.jsonl")
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        with open(chemin, "a", encoding="utf-8") as f:
            for e in executions:
                f.write(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n")
        note("executions : %d tentees, %d realisees"
             % (diag_exec["nb"], diag_exec.get("realisees", 0)))

    # 3. Detection sur l'instantane du jour
    signaux, diagnostics = incoherences.tous_les_detecteurs(
        marches, evenements, journal=note)
    signaux = [s for s in signaux if s.avantage_net() > 0]
    note("signaux retenus apres frais : %d" % len(signaux))

    # 4. Nouvelle file d'attente, chaque signal double d'un temoin
    tirage = random.Random(horodatage)
    nouvelle_attente = []
    for s in signaux:
        enr = s.en_dict()
        enr["t"] = horodatage
        enr["parts_visees"] = round(parts_pour_budget(enr["jambes"]), 3)
        nouvelle_attente.append(enr)
        temoin = fabriquer_temoin(enr, marches, tirage)
        if temoin:
            nouvelle_attente.append(temoin)
    ecrire_attente(nouvelle_attente)

    # 5. Journal
    diag_journal = journal.enregistrer(marches, horodatage)
    diag_signaux = journal.ajouter_signaux(signaux, horodatage)
    journal.ajouter_diagnostic({
        "univers": diag_univers,
        "execution": diag_exec,
        "carnets_croises": len(diagnostics["carnets_croises"]),
        "densites_negatives": len(diagnostics["densites_negatives"]),
        "journal": diag_journal,
    }, horodatage)

    poids = journal.taille_journal()
    duree = time.time() - depart

    print("\n" + "-" * 70)
    print("  univers            : %d marches negociables" % len(marches))
    print("  instantane         : %s, %d lignes, %.1f Ko compresses"
          % (diag_journal["genre"], diag_journal["lignes_ecrites"],
             diag_journal["octets_compresses"] / 1024.0))
    print("  signaux emis       : %d (+%d temoins)"
          % (len(signaux), len(nouvelle_attente) - len(signaux)))
    print("  executions         : %d realisees sur %d"
          % (diag_exec.get("realisees", 0), diag_exec["nb"]))

    gains = [e["gain_certain"] for e in executions
             if e.get("gain_certain") is not None and not e["temoin"]]
    if gains:
        gagnants = sum(1 for g in gains if g > 0)
        print("  arbitrages soldes  : %d, dont %d positifs apres frais"
              % (len(gains), gagnants))
        print("  gain garanti moyen : %+.4f $ pour %.0f $ engages"
              % (sum(gains) / len(gains), BUDGET_ESSAI))
        desequilibres = [e.get("desequilibre_parts", 0) for e in executions
                         if e.get("desequilibre_parts") is not None]
        if desequilibres and max(desequilibres) > 0.5:
            print("  ATTENTION : desequilibre de parts jusqu'a %.1f entre jambes"
                  % max(desequilibres))
    print("  journal cumule     : %.1f Mo en %d fichiers"
          % (poids["mo"], poids["fichiers"]))
    print("  duree              : %.1f s" % duree)

    anomalies = diagnostics["carnets_croises"]
    if anomalies:
        print("\n  ANOMALIE : %d carnet(s) croise(s), lecture suspecte" % len(anomalies))
        for a in anomalies[:5]:
            print("    marche %s : %s" % (a["marche_id"], a["motif"]))

    if diag_univers.get("echecs"):
        print("\n  ANOMALIE : pagination incomplete -> %s" % diag_univers["echecs"][:2])
        return 1
    return 1 if anomalies else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as err:  # noqa: BLE001
        force_utf8()
        import traceback
        traceback.print_exc()
        print("ERREUR INTERNE : %s" % err)
        sys.exit(2)
