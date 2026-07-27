"""Seconde sonde : les inconnues qui conditionnent l'architecture.

La premiere sonde a montre que toutes les sources critiques repondent. Restent
six points dont depend le dimensionnement du collecteur :

  A. Combien de marches actifs au total (Gamma plafonne limit a 100)
  B. Quelle taille de lot accepte /books  <- le plus important
  C. Ou vit la categorie, qui fixe le taux de frais
  D. Quel est le vrai point d'acces du classement des portefeuilles
  E. La bande de transactions permet-elle de modeliser un remplissage apporteur
  F. Ou casse la limite de debit
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

res = {}


def titre(t):
    print("\n" + "=" * 68)
    print(t)
    print("=" * 68)


# --- A. Volume reel de l'univers ------------------------------------------

def a_univers():
    titre("A. Combien de marches actifs, et combien sont vraiment negociables")
    total, decalage, echantillon = 0, 0, []
    debut = time.time()
    while decalage < 30000:
        p = http_json(GAMMA + "/markets",
                      {"closed": "false", "limit": 100, "offset": decalage}, essais=2)
        if not p.ok or not isinstance(p.donnees, list) or not p.donnees:
            break
        total += len(p.donnees)
        echantillon.extend(p.donnees)
        if len(p.donnees) < 100:
            break
        decalage += 100
        if time.time() - debut > 90:
            print("  (comptage arrete a 90 s)")
            break

    # Combien passent un seuil de liquidite exploitable ?
    seuils = {}
    for seuil in (0, 500, 1000, 5000, 20000):
        seuils[seuil] = sum(
            1 for m in echantillon
            if float(m.get("liquidityNum") or m.get("liquidity") or 0) >= seuil)

    actifs_ordres = sum(1 for m in echantillon if m.get("enableOrderBook"))
    avec_negrisk = sum(1 for m in echantillon if m.get("negRisk"))

    res["A_univers"] = {
        "total_non_clotures": total,
        "avec_carnet_actif": actifs_ordres,
        "en_negrisk": avec_negrisk,
        "par_seuil_de_liquidite": seuils,
    }
    print("  total non clotures      : %d" % total)
    print("  avec carnet actif       : %d" % actifs_ordres)
    print("  en negRisk              : %d" % avec_negrisk)
    for s, n in seuils.items():
        print("  liquidite >= %6d $   : %d" % (s, n))

    # Jetons pour les sondes suivantes : on prend les plus liquides.
    liquides = sorted(
        (m for m in echantillon if m.get("enableOrderBook")),
        key=lambda m: float(m.get("liquidityNum") or m.get("liquidity") or 0),
        reverse=True)[:200]
    jetons = []
    for m in liquides:
        bruts = m.get("clobTokenIds")
        if isinstance(bruts, str):
            try:
                bruts = json.loads(bruts)
            except json.JSONDecodeError:
                continue
        if bruts:
            jetons.extend(bruts)
    print("  jetons collectes pour la suite : %d" % len(jetons))
    return jetons, liquides


# --- B. Taille de lot acceptee par /books ---------------------------------

def b_lot(jetons):
    titre("B. Taille maximale d'un lot /books  (decide tout le dimensionnement)")
    trouve = {}
    for taille in (10, 50, 100, 200, 500):
        if len(jetons) < taille:
            print("  %3d : pas assez de jetons sous la main" % taille)
            continue
        debut = time.time()
        r = http_json(CLOB + "/books",
                      corps=[{"token_id": j} for j in jetons[:taille]],
                      methode="POST", essais=1, timeout=40)
        ms = (time.time() - debut) * 1000
        recus = len(r.donnees) if r.ok and isinstance(r.donnees, list) else None
        trouve[taille] = {"statut": r.statut, "recus": recus, "ms": round(ms),
                          "erreur": (r.erreur or "")[:120]}
        print("  %3d demandes -> statut %s, %s recus, %d ms  %s"
              % (taille, r.statut, recus, ms, (r.erreur or "")[:80]))
        time.sleep(1)
    res["B_lot"] = trouve


# --- C. Ou vit la categorie (elle fixe le taux de frais) -------------------

def c_categorie(liquides):
    titre("C. Ou lire la categorie, qui determine le taux de frais")
    ev = http_json(GAMMA + "/events", {"closed": "false", "limit": 2,
                                       "order": "volume24hr", "ascending": "false"})
    if ev.ok and isinstance(ev.donnees, list) and ev.donnees:
        e = ev.donnees[0]
        print("  clefs d'un evenement : %s" % ", ".join(sorted(e.keys()))[:900])
        res["C_clefs_evenement"] = sorted(e.keys())
        for champ in ("tags", "category", "series", "seriesSlug", "slug", "title"):
            if champ in e:
                print("  evenement.%s = %s" % (champ, json.dumps(e[champ], ensure_ascii=False)[:400]))
                res["C_ev_" + champ] = schema_de(e[champ], 2)

    if liquides:
        m = liquides[0]
        print("\n  clefs d'un marche : %s" % ", ".join(sorted(m.keys()))[:1400])
        res["C_clefs_marche"] = sorted(m.keys())
        for champ in ("category", "tags", "events", "slug", "question", "groupItemTitle",
                      "umaResolutionStatus", "fee", "makerBaseFee", "takerBaseFee"):
            if champ in m:
                print("  marche.%s = %s" % (champ, json.dumps(m[champ], ensure_ascii=False)[:300]))

    # Polymarket expose-t-il ses taux de frais ?
    for chemin in ("/fee-rate-bps", "/markets", "/rewards/markets"):
        r = http_json(CLOB + chemin, essais=1, timeout=15)
        if r.ok:
            ech = r.donnees
            if isinstance(ech, dict) and "data" in ech:
                liste = ech["data"]
                ech = liste[0] if liste else None
            elif isinstance(ech, list):
                ech = ech[0] if ech else None
            print("\n  CLOB%s -> %s" % (chemin, json.dumps(schema_de(ech, 2), ensure_ascii=False)[:900]))
            res["C_clob" + chemin.replace("/", "_")] = schema_de(ech, 2)
        else:
            print("\n  CLOB%s -> %s" % (chemin, r.resume()[:120]))


# --- D. Le classement des portefeuilles -----------------------------------

def d_classement():
    titre("D. Retrouver le classement des portefeuilles (404 au premier essai)")
    essais = [
        (DATA + "/leaderboard", {"window": "30d", "limit": 5}),
        (DATA + "/rankings", {"window": "30d", "limit": 5}),
        (DATA + "/traders", {"limit": 5}),
        (DATA + "/pnl-leaderboard", {"limit": 5}),
        (GAMMA + "/leaderboard", {"limit": 5}),
        ("https://lb-api.polymarket.com/leaderboard", {"window": "30d", "limit": 5}),
        ("https://lb-api.polymarket.com/profit", {"window": "30d", "limit": 5}),
        ("https://lb-api.polymarket.com/volume", {"window": "30d", "limit": 5}),
    ]
    for url, params in essais:
        r = http_json(url, params, essais=1, timeout=15)
        marque = "TROUVE" if r.ok else "      "
        print("  %s %-52s %s" % (marque, url.replace("https://", ""), r.resume()[:90]))
        if r.ok:
            res["D_" + url.split("/")[-1]] = schema_de(r.donnees, 3)
            print("         %s" % json.dumps(schema_de(r.donnees, 3), ensure_ascii=False)[:700])


# --- E. La bande de transactions (indispensable au remplissage apporteur) --

def e_transactions(liquides):
    titre("E. La bande de transactions permet-elle de modeliser un ordre a cours limite")
    cond = liquides[0].get("conditionId") if liquides else None

    r = http_json(DATA + "/trades", {"limit": 5, "market": cond}, essais=2)
    print("  /trades filtre par marche : %s" % r.resume()[:100])
    if r.ok and isinstance(r.donnees, list) and r.donnees:
        t = r.donnees[0]
        print("  clefs : %s" % ", ".join(sorted(t.keys())))
        print("  exemple : %s" % json.dumps(t, ensure_ascii=False)[:700])
        res["E_transaction"] = sorted(t.keys())
        manquants = [c for c in ("price", "size", "side", "timestamp") if c not in t]
        if manquants:
            print("  ATTENTION champs absents : %s" % manquants)

    # Peut-on remonter le temps ? Sans cela, pas de remplissage apporteur credible.
    gros = http_json(DATA + "/trades", {"limit": 500}, essais=2)
    if gros.ok and isinstance(gros.donnees, list):
        n = len(gros.donnees)
        horod = [t.get("timestamp") for t in gros.donnees if t.get("timestamp")]
        etendue = None
        if horod:
            try:
                etendue = (max(int(h) for h in horod) - min(int(h) for h in horod))
            except (TypeError, ValueError):
                pass
        print("\n  /trades?limit=500 -> %d transactions, etendue %s s" % (n, etendue))
        res["E_debit_transactions"] = {"nb": n, "etendue_s": etendue}
        if etendue is not None and etendue < 300:
            print("  NOTE : moins de 5 min d'historique par appel, il faudra paginer")

    dec = http_json(DATA + "/trades", {"limit": 5, "offset": 500}, essais=1)
    print("  pagination par offset : %s" % dec.resume()[:100])


# --- F. Ou casse la limite de debit ---------------------------------------

def f_debit(jetons):
    titre("F. Ou casse la limite de debit du CLOB")
    if len(jetons) < 20:
        print("  pas assez de jetons")
        return
    mesures = {}
    for cible in (10, 20, 40):
        codes, debut = [], time.time()
        for i in range(30):
            r = http_json(CLOB + "/midpoint", {"token_id": jetons[i % len(jetons)]},
                          essais=1, timeout=8)
            codes.append(r.statut if r.statut is not None else 0)
            attendu = i / cible
            reel = time.time() - debut
            if reel < attendu:
                time.sleep(attendu - reel)
        duree = time.time() - debut
        n429 = sum(1 for c in codes if c == 429)
        mesures[cible] = {"reel_par_s": round(30 / duree, 1), "nb_429": n429}
        print("  cible %2d/s -> reel %.1f/s, %d refus 429" % (cible, 30 / duree, n429))
        time.sleep(3)
    res["F_debit"] = mesures


def main():
    force_utf8()
    print("SONDE 2 - %s" % maintenant_iso())
    jetons, liquides = a_univers()
    b_lot(jetons)
    c_categorie(liquides)
    d_classement()
    e_transactions(liquides)
    f_debit(jetons)

    res["_horodatage"] = maintenant_iso()
    with open("sonde2-resultat.json", "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)
    print("\nTermine.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as err:  # noqa: BLE001
        force_utf8()
        import traceback
        traceback.print_exc()
        print("ERREUR INTERNE : %s" % err)
        sys.exit(2)
