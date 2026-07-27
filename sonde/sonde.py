"""Sonde des sources de donnees.

Interroge chaque source une fois, releve le schema reel, les volumes et le
comportement en limite de debit. Tourne sur GitHub Actions parce que les
domaines Polymarket sont bloques au niveau du resolveur en France.

Sortie : resume lisible sur stdout + sonde-resultat.json complet.
Codes de sortie : 0 tout va bien, 1 une source critique est morte, 2 erreur interne.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from outils.commun import force_utf8, http_json, maintenant_iso, schema_de  # noqa: E402

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"

# Sources dont l'echec fait echouer la sonde : sans elles il n'y a pas de mission.
CRITIQUES = {"gamma_evenements", "clob_carnet", "data_transactions"}

resultats = {}
notes = []


def enregistrer(cle, rep, extra=None):
    """Range une reponse dans les resultats et l'affiche de facon compacte."""
    entree = {
        "url": rep.url,
        "statut": rep.statut,
        "ms": round(rep.ms),
        "ok": rep.ok,
        "erreur": rep.erreur,
    }
    if rep.ok:
        entree["schema"] = schema_de(rep.donnees)
    if extra:
        entree.update(extra)
    resultats[cle] = entree
    print("\n[%s] %s" % (cle, rep.resume()))
    if rep.ok:
        print(json.dumps(entree.get("schema"), indent=2, ensure_ascii=False)[:2600])
    if extra:
        for k, v in extra.items():
            if k != "schema":
                print("  %s = %s" % (k, json.dumps(v, ensure_ascii=False)[:600]))
    return rep


# --------------------------------------------------------------------------
# Polymarket - Gamma (metadonnees des evenements et marches)
# --------------------------------------------------------------------------

def sonde_gamma():
    rep = http_json(GAMMA + "/events", {
        "closed": "false", "limit": 3, "order": "volume24hr", "ascending": "false",
    })
    enregistrer("gamma_evenements", rep)

    negrisk = 0
    exemple_negrisk = None
    if rep.ok and isinstance(rep.donnees, list):
        for ev in rep.donnees:
            if ev.get("negRisk"):
                negrisk += 1
                if exemple_negrisk is None:
                    exemple_negrisk = {
                        "slug": ev.get("slug"),
                        "nb_marches": len(ev.get("markets") or []),
                    }
        notes.append("Gamma /events renvoie %d evenements, dont %d en negRisk."
                     % (len(rep.donnees), negrisk))

    rep_m = http_json(GAMMA + "/markets", {
        "closed": "false", "limit": 3, "order": "volume24hr", "ascending": "false",
    })
    enregistrer("gamma_marches", rep_m, {"exemple_negrisk": exemple_negrisk})

    # Combien de marches actifs au total ? On pagine par pas de 500 sans lire le detail.
    total = 0
    decalage = 0
    debut = time.time()
    while decalage < 20000:
        page = http_json(GAMMA + "/markets", {
            "closed": "false", "limit": 500, "offset": decalage,
        }, essais=2)
        if not page.ok or not isinstance(page.donnees, list) or not page.donnees:
            break
        total += len(page.donnees)
        if len(page.donnees) < 500:
            break
        decalage += 500
        if time.time() - debut > 60:
            notes.append("Comptage des marches interrompu a 60 s (>= %d marches)." % total)
            break
    resultats["gamma_nb_marches_actifs"] = total
    print("\n[gamma_nb_marches_actifs] %d marches non clotures" % total)

    # De quoi alimenter les sondes suivantes.
    jetons, condition_id, marche_exemple = [], None, None
    if rep_m.ok and isinstance(rep_m.donnees, list):
        for m in rep_m.donnees:
            bruts = m.get("clobTokenIds")
            if isinstance(bruts, str):
                try:
                    bruts = json.loads(bruts)
                except json.JSONDecodeError:
                    bruts = None
            if bruts:
                jetons = list(bruts)
                condition_id = m.get("conditionId")
                marche_exemple = {
                    "question": m.get("question"),
                    "conditionId": condition_id,
                    "clobTokenIds": jetons,
                    "outcomes": m.get("outcomes"),
                    "outcomePrices": m.get("outcomePrices"),
                    "negRisk": m.get("negRisk"),
                    "liquidite": m.get("liquidityNum") or m.get("liquidity"),
                    "volume24h": m.get("volume24hr"),
                    "fin": m.get("endDate"),
                    "categorie": m.get("category"),
                }
                break
    resultats["marche_exemple"] = marche_exemple
    print("\n[marche_exemple] %s" % json.dumps(marche_exemple, ensure_ascii=False)[:900])
    return jetons, condition_id


# --------------------------------------------------------------------------
# Polymarket - CLOB (carnets d'ordres, la donnee qui fait tout le travail)
# --------------------------------------------------------------------------

def sonde_clob(jetons):
    if not jetons:
        notes.append("ALERTE : aucun jeton CLOB recupere, les sondes carnet sont sautees.")
        return

    jeton = jetons[0]

    rep = http_json(CLOB + "/book", {"token_id": jeton})
    profondeur = None
    if rep.ok and isinstance(rep.donnees, dict):
        achats = rep.donnees.get("bids") or []
        ventes = rep.donnees.get("asks") or []
        profondeur = {
            "nb_niveaux_achat": len(achats),
            "nb_niveaux_vente": len(ventes),
            "meilleur_achat": achats[-1] if achats else None,
            "meilleure_vente": ventes[-1] if ventes else None,
        }
    enregistrer("clob_carnet", rep, {"profondeur": profondeur})

    # Le lot est decisif : sans lui, 800 marches x 2 jetons = 1600 appels par cycle.
    lot = http_json(CLOB + "/books", corps=[{"token_id": j} for j in jetons[:2]],
                    methode="POST")
    enregistrer("clob_carnets_lot", lot, {
        "nb_demandes": len(jetons[:2]),
        "nb_recus": len(lot.donnees) if lot.ok and isinstance(lot.donnees, list) else None,
    })

    for chemin, params in (
        ("/midpoint", {"token_id": jeton}),
        ("/spread", {"token_id": jeton}),
        ("/price", {"token_id": jeton, "side": "buy"}),
        ("/prices-history", {"market": jeton, "interval": "1w", "fidelity": 60}),
        ("/sampling-markets", {}),
    ):
        r = http_json(CLOB + chemin, params, essais=2)
        extra = None
        if chemin == "/prices-history" and r.ok and isinstance(r.donnees, dict):
            hist = r.donnees.get("history") or []
            extra = {"nb_points": len(hist), "premier": hist[0] if hist else None}
        enregistrer("clob" + chemin.replace("/", "_"), r, extra)

    # Limite de debit reelle : 25 appels aussi vite que possible.
    codes, depart = [], time.time()
    for _ in range(25):
        r = http_json(CLOB + "/midpoint", {"token_id": jeton}, essais=1, timeout=10)
        codes.append(r.statut if r.statut is not None else "reseau")
    duree = time.time() - depart
    nb_429 = sum(1 for c in codes if c == 429)
    resultats["clob_limite_debit"] = {
        "nb_appels": 25,
        "duree_s": round(duree, 2),
        "appels_par_s": round(25 / duree, 1) if duree else None,
        "nb_429": nb_429,
        "codes": codes,
    }
    print("\n[clob_limite_debit] 25 appels en %.1f s, %d refus 429" % (duree, nb_429))
    if nb_429:
        notes.append("CLOB limite le debit : %d/25 appels refuses a %.1f appels/s."
                     % (nb_429, 25 / duree if duree else 0))


# --------------------------------------------------------------------------
# Polymarket - Data API (transactions, classement, portefeuilles : le gisement on-chain)
# --------------------------------------------------------------------------

def sonde_data(condition_id):
    rep = http_json(DATA + "/trades", {"limit": 3, "takerOnly": "false"})
    enregistrer("data_transactions", rep)

    adresse = None
    if rep.ok and isinstance(rep.donnees, list) and rep.donnees:
        prem = rep.donnees[0]
        adresse = prem.get("proxyWallet") or prem.get("maker") or prem.get("taker")

    for cle, chemin, params in (
        ("data_classement", "/leaderboard", {"window": "30d", "limit": 5, "orderBy": "pnl"}),
        ("data_classement_alt", "/leaderboard", {"limit": 5}),
        ("data_porteurs", "/holders", {"market": condition_id, "limit": 5}),
    ):
        enregistrer(cle, http_json(DATA + chemin, params, essais=2))

    if adresse:
        for cle, chemin in (("data_positions", "/positions"), ("data_activite", "/activity")):
            enregistrer(cle, http_json(DATA + chemin, {"user": adresse, "limit": 5}, essais=2),
                        {"adresse_testee": adresse})
    else:
        notes.append("ALERTE : aucune adresse de portefeuille extraite des transactions.")


# --------------------------------------------------------------------------
# Sources externes de reference
# --------------------------------------------------------------------------

def sonde_externes():
    enregistrer("kalshi", http_json(
        "https://api.elections.kalshi.com/trade-api/v2/markets",
        {"limit": 3, "status": "open"}, essais=2))

    r = http_json("https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
                  {"currency": "BTC", "kind": "option"}, essais=2, timeout=30)
    extra = None
    if r.ok and isinstance(r.donnees, dict):
        liste = r.donnees.get("result") or []
        extra = {"nb_options_btc": len(liste)}
    enregistrer("deribit_options", r, extra)

    enregistrer("openmeteo", http_json("https://api.open-meteo.com/v1/forecast", {
        "latitude": 40.71, "longitude": -74.01,
        "daily": "temperature_2m_max", "forecast_days": 3, "timezone": "UTC",
    }, essais=2))

    enregistrer("manifold", http_json("https://api.manifold.markets/v0/markets",
                                      {"limit": 3}, essais=2))

    enregistrer("metaculus", http_json("https://www.metaculus.com/api2/questions/",
                                       {"limit": 3, "status": "open"}, essais=2))

    # Cotes sportives sans cle : on verifie ce qui repond en acces libre.
    enregistrer("oddspapi_libre", http_json("https://api.oddspapi.io/v1/sports",
                                            essais=1, timeout=15))


def main():
    force_utf8()
    print("=" * 72)
    print("SONDE DES SOURCES - %s" % maintenant_iso())
    print("=" * 72)

    jetons, condition_id = sonde_gamma()
    sonde_clob(jetons)
    sonde_data(condition_id)
    sonde_externes()

    resultats["_notes"] = notes
    resultats["_horodatage"] = maintenant_iso()

    with open("sonde-resultat.json", "w", encoding="utf-8") as f:
        json.dump(resultats, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 72)
    print("SYNTHESE")
    print("=" * 72)
    morts_critiques = []
    for cle, val in sorted(resultats.items()):
        if not isinstance(val, dict) or "ok" not in val:
            continue
        etat = "OK  " if val["ok"] else "MORT"
        print("  %s  %-24s %5s ms  %s" % (
            etat, cle, val.get("ms", "?"),
            "" if val["ok"] else str(val.get("erreur"))[:90]))
        if not val["ok"] and cle in CRITIQUES:
            morts_critiques.append(cle)

    for n in notes:
        print("  NOTE : %s" % n)

    if morts_critiques:
        print("\nECHEC : source critique injoignable -> %s" % ", ".join(morts_critiques))
        return 1
    print("\nToutes les sources critiques repondent.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as err:  # noqa: BLE001
        force_utf8()
        print("ERREUR INTERNE : %s: %s" % (type(err).__name__, err))
        import traceback
        traceback.print_exc()
        sys.exit(2)
