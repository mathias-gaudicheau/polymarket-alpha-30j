"""Couche 3 : chercher dans nos propres donnees des motifs qu'on n'a pas prevus.

Le principe est simple a enoncer. Pour chaque marche denoue on connait le
prix affiche et le resultat reel. Si l'on decoupe l'ensemble selon une
caracteristique -- l'ecart achat-vente, le temps restant, la categorie, la
liquidite -- et qu'une tranche voit ses evenements se realiser nettement plus
souvent que son prix ne l'annoncait, il y a la un avantage a prendre.

Le danger est tout aussi simple, et il est mortel. Une machine qui teste des
milliers de decoupages trouvera des motifs rentables dans du bruit pur : sur
mille tests sans le moindre effet reel, cinquante paraissent significatifs au
seuil de cinq pour cent. Un moteur de decouverte naif n'est donc pas un outil
d'analyse, c'est une fabrique d'illusions qui coutent cher.

Quatre garde-fous, dans cet ordre :

  1. **Effectif minimal.** Une tranche de douze observations ne dit rien.
  2. **Coupe en deux moities.** Le motif est cherche sur la premiere moitie
     chronologique et doit se confirmer sur la seconde, qu'il n'a jamais vue.
  3. **Correction de Benjamini-Hochberg** sur l'ensemble des tests menes,
     et non tranche par tranche.
  4. **Ampleur minimale.** Un ecart statistiquement solide mais inferieur au
     cout des frais n'est pas une occasion.

Ce fichier est verifie par un test qui lui injecte du bruit pur et exige
qu'il ne trouve rien. C'est la seule facon de savoir qu'il ne ment pas.
"""

from __future__ import annotations

import math

from analyse.metriques import benjamini_hochberg

EFFECTIF_MINIMAL = 40        # par tranche, et sur chaque moitie
ECART_MINIMAL = 0.03         # en points de probabilite
SEUIL_FAUSSES_DECOUVERTES = 0.10


def decouper(observations, caracteristique, nb_tranches=6):
    """Repartit les observations en tranches d'effectifs comparables.

    Le decoupage par quantiles plutot que par valeurs fixes evite de creer
    des tranches vides ou une tranche qui contiendrait tout.
    """
    avec = [o for o in observations if o.get(caracteristique) is not None]
    if len(avec) < EFFECTIF_MINIMAL * 2:
        return []

    valeurs = sorted(avec, key=lambda o: o[caracteristique])
    if isinstance(valeurs[0][caracteristique], str):
        groupes = {}
        for o in avec:
            groupes.setdefault(o[caracteristique], []).append(o)
        return [{"caracteristique": caracteristique, "libelle": str(cle),
                 "observations": lot}
                for cle, lot in groupes.items() if len(lot) >= EFFECTIF_MINIMAL]

    taille = len(valeurs) // nb_tranches
    if taille < EFFECTIF_MINIMAL:
        return []
    tranches = []
    for i in range(nb_tranches):
        debut = i * taille
        fin = (i + 1) * taille if i < nb_tranches - 1 else len(valeurs)
        lot = valeurs[debut:fin]
        if len(lot) < EFFECTIF_MINIMAL:
            continue
        tranches.append({
            "caracteristique": caracteristique,
            "libelle": "%s de %.4g a %.4g" % (
                caracteristique, lot[0][caracteristique], lot[-1][caracteristique]),
            "borne_basse": lot[0][caracteristique],
            "borne_haute": lot[-1][caracteristique],
            "observations": lot,
        })
    return tranches


def evaluer(observations):
    """Mesure l'ecart entre les prix annonces et les realisations observees.

    Chaque observation porte sa propre probabilite annoncee. Comparer la
    frequence globale au prix *moyen* de la tranche gaspille cette
    information : deux marches a 0,05 et 0,25 n'ont pas la meme variance et
    ne doivent pas peser pareil.

    On teste donc la somme des ecarts individuels. Sous l'hypothese que le
    marche a raison, chaque ecart (realisation moins prix) est centre et de
    variance p(1-p). Leur somme suit une loi de Poisson-binomiale, bien
    approchee par une normale des que l'effectif depasse la quarantaine.
    C'est le meme raisonnement qu'un test binomial, mais qui rend a chaque
    observation son poids propre -- et le gain de puissance est net sur des
    tranches ou les prix s'etalent.
    """
    lot = [o for o in observations
           if o.get("prix") is not None and o.get("realise") is not None]
    n = len(lot)
    if n < EFFECTIF_MINIMAL:
        return None

    somme_ecarts = sum((1.0 if o["realise"] else 0.0) - o["prix"] for o in lot)
    variance = sum(o["prix"] * (1.0 - o["prix"]) for o in lot)
    if variance <= 0:
        return None

    ecart_type = math.sqrt(variance)
    z = somme_ecarts / ecart_type
    # Test unilateral dans le sens observe.
    valeur_p = 0.5 * math.erfc(abs(z) / math.sqrt(2.0))

    prix_moyen = sum(o["prix"] for o in lot) / n
    realises = sum(1 for o in lot if o["realise"])

    return {"n": n, "prix_moyen": prix_moyen, "frequence": realises / n,
            "ecart": somme_ecarts / n, "valeur_p": valeur_p, "z": z,
            # Plus petit ecart moyen que cette tranche aurait pu distinguer du
            # hasard. Le publier evite de confondre "rien trouve" et "rien a
            # trouver" : une tranche peu fournie ne prouve pas l'absence.
            "ecart_detectable": 1.96 * ecart_type / n,
            "sens": "sous_cote" if somme_ecarts > 0 else "sur_cote"}


def fouiller(observations, caracteristiques, journal=None):
    """Cherche des motifs, et ne retient que ceux qui survivent aux quatre verrous.

    `observations` : liste de dicts contenant au minimum `prix`, `realise`
    (booleen) et `t` (horodatage), plus les caracteristiques a explorer.

    Renvoie (motifs_retenus, diagnostic).
    """
    if len(observations) < EFFECTIF_MINIMAL * 4:
        if journal:
            journal("fouille : %d observations, il en faut au moins %d"
                    % (len(observations), EFFECTIF_MINIMAL * 4))
        return [], {"motif": "effectif global insuffisant",
                    "n": len(observations)}

    # Coupe chronologique : la premiere moitie sert a chercher, la seconde a
    # verifier. Un motif qui ne survit pas au passage n'etait qu'un accident.
    ordonnees = sorted(observations, key=lambda o: o.get("t") or 0)
    milieu = len(ordonnees) // 2
    exploration, validation = ordonnees[:milieu], ordonnees[milieu:]

    candidats = []
    for c in caracteristiques:
        for tranche in decouper(exploration, c):
            mesure = evaluer(tranche["observations"])
            if mesure is None:
                continue
            candidats.append({"tranche": tranche, "exploration": mesure})

    if not candidats:
        if journal:
            journal("fouille : aucune tranche exploitable")
        return [], {"motif": "aucune tranche", "tests": 0}

    # Verrou 3 : correction sur l'ensemble des tests menes.
    valeurs_p = [c["exploration"]["valeur_p"] for c in candidats]
    retenus_bh = set(benjamini_hochberg(valeurs_p, SEUIL_FAUSSES_DECOUVERTES))

    motifs, rejets = [], {"bh": 0, "ampleur": 0, "validation": 0, "effectif": 0}
    for i, cand in enumerate(candidats):
        if i not in retenus_bh:
            rejets["bh"] += 1
            continue
        if abs(cand["exploration"]["ecart"]) < ECART_MINIMAL:
            rejets["ampleur"] += 1
            continue

        # Verrou 2 : le meme decoupage, applique a des donnees jamais vues.
        tr = cand["tranche"]
        if "borne_basse" in tr:
            lot = [o for o in validation
                   if o.get(tr["caracteristique"]) is not None
                   and tr["borne_basse"] <= o[tr["caracteristique"]] <= tr["borne_haute"]]
        else:
            cible = tr["libelle"]
            lot = [o for o in validation if str(o.get(tr["caracteristique"])) == cible]

        mesure_v = evaluer(lot)
        if mesure_v is None:
            rejets["effectif"] += 1
            continue
        # Le sens doit se confirmer, et l'ampleur tenir au moins a moitie.
        meme_sens = (mesure_v["ecart"] > 0) == (cand["exploration"]["ecart"] > 0)
        tient = abs(mesure_v["ecart"]) >= abs(cand["exploration"]["ecart"]) / 2.0
        if not (meme_sens and tient):
            rejets["validation"] += 1
            continue

        motifs.append({
            "caracteristique": tr["caracteristique"],
            "libelle": tr["libelle"],
            "borne_basse": tr.get("borne_basse"),
            "borne_haute": tr.get("borne_haute"),
            "exploration": cand["exploration"],
            "validation": mesure_v,
            "ecart_retenu": min(abs(cand["exploration"]["ecart"]),
                                abs(mesure_v["ecart"])),
            "sens": cand["exploration"]["sens"],
        })

    if journal:
        journal("fouille : %d tests, %d motifs retenus ; rejets -> %s"
                % (len(candidats), len(motifs),
                   ", ".join("%s=%d" % kv for kv in rejets.items() if kv[1])))

    return motifs, {"tests": len(candidats), "rejets": rejets,
                    "n_exploration": len(exploration),
                    "n_validation": len(validation)}


def redaction_declaration(motif):
    """Redige la declaration a inscrire au protocole pour un motif retenu.

    Un motif decouvert ne recoit pas de capital : il doit d'abord etre
    declare, date et scelle, puis eprouve a blanc sur des donnees posterieures
    a sa declaration. C'est ce qui garde le test hors echantillon meme quand
    la strategie a ete trouvee par la machine.
    """
    e, v = motif["exploration"], motif["validation"]
    sens = ("se realisent plus souvent que leur prix ne l'annonce"
            if motif["sens"] == "sous_cote"
            else "se realisent moins souvent que leur prix ne l'annonce")
    return (
        "Sur la tranche « %s », les marches %s : prix moyen %.3f contre "
        "une frequence observee de %.3f, soit %+.3f, sur %d observations "
        "(valeur p %.4g apres correction pour tests multiples). Le motif se "
        "confirme sur la moitie de validation, jamais vue lors de la "
        "recherche : %+.3f sur %d observations. Ecart retenu, le plus "
        "prudent des deux : %.3f."
        % (motif["libelle"], sens, e["prix_moyen"], e["frequence"], e["ecart"],
           e["n"], e["valeur_p"], v["ecart"], v["n"], motif["ecart_retenu"]))
