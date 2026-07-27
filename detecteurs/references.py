"""Couche 2 : confronter Polymarket a des marches plus efficients.

A la difference de la couche 1, ces signaux ne sont pas des arbitrages. Ils
portent un vrai risque : on affirme qu'une source exterieure estime mieux la
probabilite que Polymarket, ce qui reste une opinion tant que les
denouements ne l'ont pas confirmee. Trois precautions en decoulent.

D'abord un abattement de confiance. Une probabilite d'options est
risque-neutre, pas reelle : elle contient une prime de risque, et elle
suppose que les deux places reglent sur le meme indice a la meme heure.
Plutot que de parier sur l'ecart brut, on n'en retient qu'une fraction. La
position estimee est tiree depuis le prix de marche vers la reference, sans
jamais l'atteindre.

Ensuite un poids appris. Cette fraction n'est pas decretee une fois pour
toutes : elle se mesure sur nos propres denouements. Si la source se revele
moins bonne que le prix Polymarket, son poids tombe, et avec lui les mises.

Enfin un plafond d'incredulite. Un ecart enorme entre deux marches liquides
signale presque toujours un appariement errone -- mauvaise echeance, mauvais
seuil, mauvais actif -- et non une occasion. On le consigne sans le jouer.
"""

from __future__ import annotations

from detecteurs.socle import Jambe, Signal, prix_negociables
from moteur import frais as mf

# Fraction de l'ecart retenue avant tout apprentissage. Une source neuve est
# creditee de la moitie du chemin, jamais de la totalite.
POIDS_INITIAL = 0.5

# En deca, l'ecart ne vaut pas la peine d'etre distingue du bruit de cotation.
ECART_MINIMAL = 0.03

# Au-dela, on soupconne un appariement errone plutot qu'une occasion. Deux
# marches liquides portant reellement sur la meme chose ne divergent pas de
# vingt-cinq points.
ECART_INCROYABLE = 0.25

# Marge exigee au-dessus du seul cout des frais.
MARGE_SUR_FRAIS = 1.5


def signaux_de_reference(appariements, poids_par_source=None, journal=None):
    """Transforme les appariements en signaux, ou en rejets consignes."""
    poids_par_source = poids_par_source or {}
    signaux = []
    rejets = {"ecart_faible": 0, "ecart_incroyable": 0, "non_cotable": 0,
              "sous_les_frais": 0, "poids_nul": 0}
    ecarts_observes = []

    for app in appariements:
        m = app["marche"]
        if not prix_negociables(m):
            rejets["non_cotable"] += 1
            continue

        source = app["source"]
        poids = poids_par_source.get(source, POIDS_INITIAL)
        if poids <= 0:
            rejets["poids_nul"] += 1
            continue

        proba = app["proba"]
        achat, vente = m["achat"], m["vente"]
        milieu = (achat + vente) / 2.0
        ecart_brut = proba - milieu
        ecarts_observes.append(ecart_brut)

        if abs(ecart_brut) > ECART_INCROYABLE:
            rejets["ecart_incroyable"] += 1
            continue
        if abs(ecart_brut) < ECART_MINIMAL:
            rejets["ecart_faible"] += 1
            continue

        # Abattement : on ne retient qu'une fraction du chemin vers la source.
        proba_retenue = milieu + poids * ecart_brut

        if ecart_brut > 0:
            # La reference juge l'evenement plus probable que le marche :
            # on achete le OUI, au prix de vente affiche.
            sens, prix, jeton = "achat", vente, m["jeton_oui"]
            avantage = proba_retenue - prix
        else:
            # La reference le juge moins probable : on achete le NON.
            sens, prix, jeton = "achat", round(1.0 - achat, 6), m["jeton_non"]
            avantage = (1.0 - proba_retenue) - prix

        if avantage <= 0:
            rejets["sous_les_frais"] += 1
            continue

        seuil = mf.seuil_d_avantage(prix, m["categorie"], MARGE_SUR_FRAIS)
        if avantage < seuil:
            rejets["sous_les_frais"] += 1
            continue

        jambe = Jambe(m["id"], jeton, sens, prix, m["categorie"], m["taux_frais"],
                      libelle=(m.get("groupe_titre") or m.get("question") or "")[:70])
        signaux.append(Signal(
            "c2_%s" % source, [jambe], avantage,
            "%s estime la probabilite a %.3f quand le marche la cote %.3f. "
            "Ecart de %+.3f, dont %.0f %% retenus apres abattement de "
            "confiance. Entree a %.4f."
            % (source.capitalize(), proba, milieu, ecart_brut, 100 * poids, prix),
            tout_ou_rien=False, reference=source,
            proba_estimee=proba_retenue, prix_reference=milieu,
            marche_pivot=m["id"]))

    if journal:
        journal("references %s : %d signaux ; rejets -> %s"
                % (appariements[0]["source"] if appariements else "-",
                   len(signaux),
                   ", ".join("%s=%d" % kv for kv in rejets.items() if kv[1])))
        if ecarts_observes:
            ecarts_observes.sort()
            n = len(ecarts_observes)
            journal("   ecarts observes : n=%d median=%+.4f min=%+.4f max=%+.4f"
                    % (n, ecarts_observes[n // 2], ecarts_observes[0],
                       ecarts_observes[-1]))

    return signaux, {"rejets": rejets, "ecarts": ecarts_observes}
