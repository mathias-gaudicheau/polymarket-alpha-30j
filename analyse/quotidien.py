"""Travail quotidien : denouements, comptabilite, rapport.

C'est le seul moment ou la base est reveillee. Le plan gratuit Neon accorde
100 heures de calcul par mois et la base s'endort apres cinq minutes sans
requete : quatre reveils par jour coutent une vingtaine de minutes, contre
720 heures si on l'interrogeait a chaque cycle de collecte.

Enchainement :
  1. relire le journal du depot et reconstituer les positions
  2. demander a Polymarket lesquelles se sont denouees
  3. solder, tenir la comptabilite, comparer au temoin
  4. verifier les conditions de promotion du protocole
  5. ecrire RAPPORT.md, en francais et sans jargon

Codes de sortie : 0 tout va bien, 1 anomalie signalee, 2 echec bloquant.
"""

from __future__ import annotations

import calendar
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyse import metriques  # noqa: E402
from outils.commun import force_utf8, http_json, maintenant_iso  # noqa: E402

GAMMA = "https://gamma-api.polymarket.com"
RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOURNAL = os.path.join(RACINE, "journal")

CAPITAL_INITIAL = 500.0
PART_RESERVE = 0.30          # jamais engagee
PLAFOND_PAR_POSITION = 0.02  # 2 % du capital
SEUIL_COUPE_CIRCUIT = 0.20   # 20 % de perte

# Debut officiel de l'experience : scellement de PROTOCOLE.md, le 2026-07-27
# a 16h35 UTC. Tout ce qui precede releve de la mise au point, pas de la
# mesure -- les detecteurs de cette periode comportaient des defauts qui
# fabriquaient des occasions imaginaires. Ces executions restent dans le
# journal et dans l'historique git, elles ne sont simplement pas comptees.
# Les effacer serait plus commode et moins honnete.
DEBUT_EXPERIENCE = 1785170400   # 2026-07-27 16:40 UTC
FIN_EXPERIENCE = DEBUT_EXPERIENCE + 30 * 86400   # 2026-08-26


def note(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m))


# --------------------------------------------------------------------------
# Lecture du journal
# --------------------------------------------------------------------------

def lire_executions():
    """Toutes les executions consignees, tous jours confondus."""
    sortie = []
    if not os.path.isdir(JOURNAL):
        return sortie
    for jour in sorted(os.listdir(JOURNAL)):
        chemin = os.path.join(JOURNAL, jour, "executions.jsonl")
        if not os.path.exists(chemin):
            continue
        with open(chemin, "r", encoding="utf-8") as f:
            for ligne in f:
                ligne = ligne.strip()
                if not ligne:
                    continue
                try:
                    sortie.append(json.loads(ligne))
                except ValueError:
                    continue
    return sortie


def positions_depuis_executions(executions):
    """Transforme les executions en positions unitaires.

    Une execution a plusieurs jambes donne plusieurs positions, toutes
    rattachees a la meme grappe : elles se denouent ensemble et ne comptent
    donc que pour une observation.
    """
    positions, avant_debut = [], 0
    for i, e in enumerate(executions):
        if not e.get("realisee"):
            continue
        if (e.get("t_execution") or 0) < DEBUT_EXPERIENCE:
            avant_debut += 1
            continue
        grappe = "%s:%s" % (e.get("strategie"), e.get("marche_pivot"))
        for j in e.get("jambes", []):
            if not j.get("rempli"):
                continue
            prix = j.get("prix_obtenu")
            parts = j.get("parts") or 0.0
            if prix is None or parts <= 0:
                continue
            positions.append({
                "execution": i,
                "strategie": e.get("strategie"),
                "temoin": bool(e.get("temoin")),
                "grappe": grappe,
                "marche_id": None,          # complete plus bas par le jeton
                "jeton": j.get("jeton"),
                "parts": parts,
                "prix_entree": prix,
                "frais": j.get("frais") or 0.0,
                "mise": parts * prix + (j.get("frais") or 0.0),
                "t_execution": e.get("t_execution"),
                # Latence reellement subie entre le signal et son execution.
                # GitHub honore les planifications au mieux, pas a la minute :
                # mieux vaut mesurer ce delai que le supposer de cinq minutes.
                "latence": ((e.get("t_execution") or 0) - (e.get("t_signal") or 0)
                            if e.get("t_signal") else None),
                "gain": None,
                "denouee": False,
            })
    if avant_debut:
        note("%d executions anterieures au scellement du protocole, ecartees "
             "du decompte (conservees au journal)" % avant_debut)
    return positions


# --------------------------------------------------------------------------
# Denouements
# --------------------------------------------------------------------------

def relever_denouements(jetons, journal=None):
    """Interroge Polymarket sur le sort des marches concernes.

    Renvoie {jeton: 1.0 si l'issue s'est realisee, 0.0 sinon}. Un marche non
    denoue n'apparait pas : une position ouverte ne se compte pas comme un
    gain, meme si elle est bien orientee.
    """
    resultats, examines = {}, 0
    jetons = list(dict.fromkeys(jetons))
    # Gamma accepte le filtrage par jeton CLOB, cent par requete.
    for depart in range(0, len(jetons), 50):
        lot = jetons[depart:depart + 50]
        rep = http_json(GAMMA + "/markets", {
            "clob_token_ids": ",".join(lot), "limit": 100,
        }, essais=2)
        if not rep.ok or not isinstance(rep.donnees, list):
            continue
        for m in rep.donnees:
            examines += 1
            if not m.get("closed"):
                continue
            try:
                jetons_m = json.loads(m.get("clobTokenIds") or "[]")
                prix = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
            except (ValueError, TypeError):
                continue
            if len(jetons_m) != len(prix):
                continue
            for jeton, p in zip(jetons_m, prix):
                # Un marche denoue cote ses issues a exactement 0 ou 1.
                if p >= 0.99:
                    resultats[jeton] = 1.0
                elif p <= 0.01:
                    resultats[jeton] = 0.0
    if journal:
        journal("denouements : %d marches examines, %d jetons soldes"
                % (examines, len(resultats)))
    return resultats


def solder(positions, denouements):
    """Calcule le gain des positions dont le marche s'est denoue.

    Une part vaut 1 $ si l'issue se realise, 0 sinon. Le gain est donc le
    produit par le resultat, moins la mise et les frais deja payes.
    """
    soldees = 0
    for p in positions:
        issue = denouements.get(p["jeton"])
        if issue is None:
            continue
        p["denouee"] = True
        p["issue"] = issue
        p["gain"] = round(p["parts"] * issue - p["mise"], 6)
        soldees += 1
    return soldees


# --------------------------------------------------------------------------
# Rapport
# --------------------------------------------------------------------------

def evaluer_alertes(gain_total, nb_executions, positions):
    """Decide des alertes du jour, coupe-circuit compris.

    Extrait dans une fonction a part pour etre testable : un coupe-circuit
    qu'on n'a jamais vu se declencher n'est pas un coupe-circuit, c'est une
    intention. Le test le force et verifie qu'il repond.
    """
    alertes = []

    if gain_total <= -CAPITAL_INITIAL * SEUIL_COUPE_CIRCUIT:
        alertes.append({
            "gravite": "CRITIQUE",
            "sujet": "coupe-circuit declenche : perte de %.2f $ sur %.0f $, soit "
                     "%.1f %% du capital. Toutes les strategies passent en veille."
                     % (-gain_total, CAPITAL_INITIAL,
                        100 * abs(gain_total) / CAPITAL_INITIAL)})

    if gain_total <= -CAPITAL_INITIAL * SEUIL_COUPE_CIRCUIT * 0.5:
        alertes.append({
            "gravite": "ALERTE",
            "sujet": "perte superieure a la moitie du seuil de coupe-circuit"})

    if not nb_executions:
        alertes.append({
            "gravite": "ALERTE",
            "sujet": "aucune execution consignee : la collecte est-elle vivante"})

    latences = sorted(p["latence"] for p in positions
                      if p.get("latence") is not None and p["latence"] > 0)
    if latences:
        mediane = latences[len(latences) // 2]
        if mediane > 900:
            alertes.append({
                "gravite": "ALERTE",
                "sujet": "latence mediane de %d s entre signal et execution : la "
                         "planification prend du retard et les occasions courtes "
                         "deviennent hors d'atteinte" % mediane})

    return alertes


def heure_de_paris(horodatage=None):
    """Formate un instant en heure française, avec le repere UTC entre parentheses.

    Toute la machinerie travaille en UTC, et doit y rester : le passage a
    l'heure d'hiver fin octobre tomberait en plein milieu de l'experience et
    decalerait toute la serie d'une heure. Mais le rapport est lu par un
    humain a Paris, a qui une heure UTC ne dit rien.

    Le decalage est calcule sans bibliotheque externe : heure d'ete du dernier
    dimanche de mars au dernier dimanche d'octobre, heure d'hiver sinon.
    """
    t = horodatage if horodatage is not None else time.time()
    ut = time.gmtime(t)

    def dernier_dimanche(annee, mois):
        # calendar.weekday calcule reellement le jour de la semaine. Une
        # premiere version passait par strftime sur un struct_time bricole :
        # strftime ne recalcule pas le jour, il fait confiance a celui qu'on
        # lui fournit, et repondait donc toujours "dimanche".
        jour = calendar.monthrange(annee, mois)[1]
        while calendar.weekday(annee, mois, jour) != calendar.SUNDAY:
            jour -= 1
        return jour

    # La bascule europeenne a lieu a 01h00 UTC, le dernier dimanche du mois.
    debut_ete = calendar.timegm(
        (ut.tm_year, 3, dernier_dimanche(ut.tm_year, 3), 1, 0, 0, 0, 0, 0))
    fin_ete = calendar.timegm(
        (ut.tm_year, 10, dernier_dimanche(ut.tm_year, 10), 1, 0, 0, 0, 0, 0))
    decalage = 2 if debut_ete <= t < fin_ete else 1

    local = time.gmtime(t + decalage * 3600)
    return "%s (%s UTC)" % (
        time.strftime("%d/%m/%Y à %Hh%M", local),
        time.strftime("%Hh%M", ut))


def _pourcent(x, defaut="-"):
    return defaut if x is None else "%+.2f %%" % (100 * x)


def construire_rapport(resumes, capital, ouvertes, alertes, diagnostic):
    lignes = []
    a = lignes.append
    a("# Rapport de l'expérience")
    a("")
    a("_Généré automatiquement le %s. Capital virtuel, aucune transaction réelle._"
      % heure_de_paris())
    a("")
    a("## Où en est le capital")
    a("")
    a("| | |")
    a("|---|---|")
    a("| Capital de départ | %.2f $ |" % CAPITAL_INITIAL)
    a("| Capital actuel | %.2f $ |" % capital["total"])
    a("| Résultat cumulé | %+.2f $ (%s) |"
      % (capital["resultat"], _pourcent(capital["resultat"] / CAPITAL_INITIAL)))
    a("| Positions encore ouvertes | %d |" % ouvertes)
    a("| Jour de l'expérience | %d sur 30 |" % capital["jour"])
    a("")

    a("## Ce que vaut chaque stratégie")
    a("")
    a("Le tableau compte en **grappes**, pas en paris : des positions qui se dénouent")
    a("ensemble ne valent qu'une observation. Sous 30 grappes dénouées, aucune conclusion")
    a("n'est énoncée — la colonne le dit explicitement plutôt que d'afficher un chiffre")
    a("qui aurait l'air d'en être un.")
    a("")
    a("| Stratégie | Paris | Grappes | Mise | Résultat | Rendement | Conclusion permise |")
    a("|---|---:|---:|---:|---:|---:|---|")
    for r in resumes:
        a("| `%s` | %d | %d | %.2f $ | %+.2f $ | %s | %s |" % (
            r["strategie"], r["n_paris"], r["n_grappes"], r["mise_totale"],
            r["gain_total"], _pourcent(r["rendement_global"]),
            "oui" if r["conclusion_permise"] else "**non — trop peu de grappes**"))
    a("")

    for r in resumes:
        if not r["n_grappes"]:
            continue
        a("### `%s`" % r["strategie"])
        a("")
        ic = r.get("intervalle")
        if ic:
            a("Rendement moyen par grappe : **%s**, intervalle de confiance à 95 %% "
              "de %s à %s sur %d grappes."
              % (_pourcent(ic["moyenne"]), _pourcent(ic["bas"]),
                 _pourcent(ic["haut"]), ic["n"]))
            if ic["bas"] <= 0:
                a("")
                a("L'intervalle contient zéro : à ce stade, rien ne distingue ce résultat "
                  "du hasard.")
        else:
            a("Trop peu de grappes dénouées pour calculer un intervalle.")
        t = r.get("temoin")
        if t:
            a("")
            a("Face au témoin aléatoire : %s contre %s, soit un écart de %s "
              "(intervalle de %s à %s). %s"
              % (_pourcent(t["reel"]), _pourcent(t["temoin"]), _pourcent(t["ecart"]),
                 _pourcent(t["bas"]), _pourcent(t["haut"]),
                 "**Écart significatif.**" if t["significatif"]
                 else "Écart non significatif : le témoin fait aussi bien."))
        a("")

    if alertes:
        a("## Alertes")
        a("")
        for al in alertes:
            a("- **%s** — %s" % (al["gravite"], al["sujet"]))
        a("")

    a("## Santé du dispositif")
    a("")
    for cle, valeur in sorted(diagnostic.items()):
        a("- %s : %s" % (cle, valeur))
    a("")
    a("---")
    a("")
    a("Les règles de chaque stratégie sont figées dans [PROTOCOLE.md](PROTOCOLE.md),")
    a("scellées par l'historique git avant que la moindre décision ne leur soit attribuée.")
    return "\n".join(lignes)


def main():
    force_utf8()
    print("=" * 70)
    print("TRAVAIL QUOTIDIEN - %s" % maintenant_iso())
    print("=" * 70)

    executions = lire_executions()
    note("executions consignees : %d" % len(executions))
    positions = positions_depuis_executions(executions)
    note("positions unitaires : %d" % len(positions))

    if not positions:
        note("aucune position : rien a solder, rapport minimal")

    jetons = [p["jeton"] for p in positions if not p["denouee"]]
    denouements = relever_denouements(jetons, journal=note) if jetons else {}
    soldees = solder(positions, denouements)
    note("positions soldees ce jour : %d" % soldees)

    denouees = [p for p in positions if p["denouee"]]
    ouvertes = len(positions) - len(denouees)

    strategies = sorted({p["strategie"] for p in denouees if not p["temoin"]})
    temoins = [p for p in denouees if p["temoin"]]
    resumes = []
    for s in strategies:
        lot = [p for p in denouees if p["strategie"] == s and not p["temoin"]]
        resumes.append(metriques.resume_strategie(lot, temoins, nom=s))

    gain_total = sum(p["gain"] or 0.0 for p in denouees if not p["temoin"])
    capital = {
        "total": CAPITAL_INITIAL + gain_total,
        "resultat": gain_total,
        "jour": max(1, len(set(
            time.strftime("%Y-%m-%d", time.gmtime(p["t_execution"]))
            for p in positions if p.get("t_execution")))),
    }

    alertes = evaluer_alertes(gain_total, len(executions), positions)

    latences = sorted(p["latence"] for p in positions
                      if p.get("latence") is not None and p["latence"] > 0)
    diagnostic = {
        "executions lues": len(executions),
        "positions ouvertes": ouvertes,
        "positions denouees": len(denouees),
        "temoins denoues": len(temoins),
    }
    if latences:
        mediane = latences[len(latences) // 2]
        diagnostic["latence mediane signal vers execution"] = (
            "%d s (min %d, max %d) — mesuree, non supposee"
            % (mediane, latences[0], latences[-1]))

    rapport = construire_rapport(resumes, capital, ouvertes, alertes, diagnostic)
    with open(os.path.join(RACINE, "RAPPORT.md"), "w", encoding="utf-8") as f:
        f.write(rapport + "\n")
    note("RAPPORT.md ecrit (%d lignes)" % len(rapport.splitlines()))

    print("\n" + "-" * 70)
    for r in resumes:
        print("  %-28s %3d paris / %2d grappes  resultat %+8.3f $"
              % (r["strategie"], r["n_paris"], r["n_grappes"], r["gain_total"]))
    print("  capital : %.2f $ (%+.2f)" % (capital["total"], capital["resultat"]))

    for al in alertes:
        print("  %s : %s" % (al["gravite"], al["sujet"]))
    return 1 if alertes else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as err:  # noqa: BLE001
        force_utf8()
        import traceback
        traceback.print_exc()
        print("ERREUR INTERNE : %s" % err)
        sys.exit(2)
