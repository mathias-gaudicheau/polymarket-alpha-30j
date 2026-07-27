"""Modele d'execution simulee.

C'est ici que la plupart des simulations mentent, et c'est donc le fichier le
plus important du projet. Trois regles le gouvernent :

  1. On ne se remplit jamais au prix median. Un ordre au marche traverse le
     carnet niveau par niveau et paie le prix moyen pondere reellement obtenu.
  2. La taille est bornee par la profondeur qui existait vraiment. Vouloir
     500 $ sur un carnet qui en offre 40 donne un remplissage de 40 $.
  3. Un ordre a cours limite n'est repute rempli que si des transactions ont
     effectivement eu lieu a ce prix ou au-dela, et seulement pour une
     fraction du volume echange : on ne rafle pas tout le flux.

Tout est en dollars et en parts. Une part vaut 1 $ si l'issue se realise, 0 sinon.
"""

from __future__ import annotations

from moteur import frais as mf

# Fraction du flux qu'on s'autorise a capter en tant qu'apporteur.
# Choix prudent : sur un carnet public, notre ordre n'est ni seul ni prioritaire.
PARTICIPATION_APPORTEUR = 0.25

# En dessous, un remplissage est traite comme nul (poussiere).
PARTS_MINIMALES = 1.0


class Carnet:
    """Un carnet d'ordres pour un jeton, normalise et trie.

    Les achats sont ranges du meilleur (le plus cher) au pire, les ventes du
    meilleur (le moins cher) au pire, quelle que soit la convention de l'API.
    """

    __slots__ = ("jeton", "achats", "ventes", "horodatage")

    def __init__(self, jeton, achats, ventes, horodatage=None):
        self.jeton = jeton
        self.achats = sorted(achats, key=lambda n: -n[0])
        self.ventes = sorted(ventes, key=lambda n: n[0])
        self.horodatage = horodatage

    @classmethod
    def depuis_api(cls, reponse, jeton=None):
        """Construit depuis la reponse de CLOB /book.

        Les prix et tailles arrivent en chaines de caracteres. Les niveaux
        illisibles sont ignores plutot que de faire echouer tout le carnet.
        """
        def niveaux(brut):
            sortie = []
            for n in brut or []:
                try:
                    prix = float(n["price"])
                    taille = float(n["size"])
                except (KeyError, TypeError, ValueError):
                    continue
                if 0.0 < prix < 1.0 and taille > 0:
                    sortie.append((prix, taille))
            return sortie

        if not isinstance(reponse, dict):
            return cls(jeton, [], [])
        return cls(
            jeton or reponse.get("asset_id") or reponse.get("market"),
            niveaux(reponse.get("bids")),
            niveaux(reponse.get("asks")),
            reponse.get("timestamp"),
        )

    @property
    def meilleur_achat(self):
        return self.achats[0][0] if self.achats else None

    @property
    def meilleure_vente(self):
        return self.ventes[0][0] if self.ventes else None

    @property
    def ecart(self):
        if self.meilleur_achat is None or self.meilleure_vente is None:
            return None
        return self.meilleure_vente - self.meilleur_achat

    @property
    def milieu(self):
        if self.meilleur_achat is None or self.meilleure_vente is None:
            return None
        return (self.meilleur_achat + self.meilleure_vente) / 2.0

    def profondeur_dollars(self, cote, limite_prix=None):
        """Montant disponible d'un cote, eventuellement borne par un prix."""
        niveaux = self.ventes if cote == "achat" else self.achats
        total = 0.0
        for prix, taille in niveaux:
            if limite_prix is not None:
                if cote == "achat" and prix > limite_prix:
                    break
                if cote == "vente" and prix < limite_prix:
                    break
            total += prix * taille
        return total

    def est_exploitable(self) -> bool:
        return bool(self.achats and self.ventes and self.ecart is not None
                    and self.ecart < 0.5)


class Remplissage:
    """Resultat d'une tentative d'execution."""

    __slots__ = ("parts", "prix_moyen", "frais", "montant_brut", "montant_net",
                 "niveaux_traverses", "borne_par_profondeur", "motif")

    def __init__(self, parts=0.0, prix_moyen=0.0, frais=0.0, montant_brut=0.0,
                 montant_net=0.0, niveaux=0, borne=False, motif=None):
        self.parts = parts
        self.prix_moyen = prix_moyen
        self.frais = frais
        self.montant_brut = montant_brut
        self.montant_net = montant_net
        self.niveaux_traverses = niveaux
        self.borne_par_profondeur = borne
        self.motif = motif

    @property
    def rempli(self) -> bool:
        return self.parts >= PARTS_MINIMALES

    def __repr__(self):
        if not self.rempli:
            return "<Remplissage vide : %s>" % (self.motif or "aucune profondeur")
        return ("<Remplissage %.1f parts a %.4f, frais %.4f $, %d niveaux%s>"
                % (self.parts, self.prix_moyen, self.frais, self.niveaux_traverses,
                   ", borne" if self.borne_par_profondeur else ""))


def executer_preneur(carnet: Carnet, cote: str, montant_vise=None,
                     categorie: str = "inconnu", prix_maximal=None,
                     parts_visees=None, taux_frais=None) -> Remplissage:
    """Ordre au marche : on traverse le carnet et on paie ce qu'il en coute.

    cote          'achat' pour acquerir des parts, 'vente' pour en ceder
    montant_vise  cible exprimee en dollars
    parts_visees  cible exprimee en parts ; prioritaire sur le montant

    La distinction entre les deux cibles n'est pas cosmetique. Un arbitrage de
    panier exige le meme nombre de parts sur chaque jambe : acheter dix
    dollars d'un jeton a trois centimes et dix dollars d'un jeton a
    quatre-vingt-quinze centimes ne donne pas un arbitrage mais deux paris
    directionnels sans rapport, dont les frais explosent du cote bon marche.

    prix_maximal  refus d'aller au-dela (l'occasion a disparu, on ne la subit pas)
    taux_frais    taux reel du marche ; a defaut, deduit de la categorie
    """
    if parts_visees is None and montant_vise is None:
        return Remplissage(motif="aucune cible")
    if parts_visees is not None and parts_visees <= 0:
        return Remplissage(motif="cible en parts nulle")
    if parts_visees is None and montant_vise <= 0:
        return Remplissage(motif="montant nul")

    niveaux = carnet.ventes if cote == "achat" else carnet.achats
    if not niveaux:
        return Remplissage(motif="carnet vide du cote %s" % cote)

    parts, cout, traverses = 0.0, 0.0, 0
    borne = True
    for prix, taille in niveaux:
        if prix_maximal is not None:
            if cote == "achat" and prix > prix_maximal:
                break
            if cote == "vente" and prix < prix_maximal:
                break

        if parts_visees is not None:
            restant_parts = parts_visees - parts
            if restant_parts <= 1e-9:
                borne = False
                break
            parts_ici = min(taille, restant_parts)
        else:
            restant = montant_vise - cout
            if restant <= 1e-9:
                borne = False
                break
            parts_ici = min(taille, restant / prix)

        if parts_ici <= 0:
            continue
        parts += parts_ici
        cout += parts_ici * prix
        traverses += 1

        if parts_visees is not None:
            if parts >= parts_visees - 1e-9:
                borne = False
                break
        elif cout >= montant_vise - 1e-9:
            borne = False
            break

    if parts < PARTS_MINIMALES:
        return Remplissage(motif="profondeur insuffisante (%.2f parts)" % parts)

    prix_moyen = cout / parts
    if taux_frais is not None:
        f = abs(parts) * taux_frais * prix_moyen * (1.0 - prix_moyen)
    else:
        f = mf.frais_preneur(parts, prix_moyen, categorie)

    # A l'achat on debourse le cout plus les frais ; a la vente on encaisse moins les frais.
    net = -(cout + f) if cote == "achat" else (cout - f)
    return Remplissage(parts, prix_moyen, f, cout, net, traverses, borne)


def executer_apporteur(prix_limite: float, cote: str, transactions,
                       montant_vise: float, categorie: str,
                       participation: float = PARTICIPATION_APPORTEUR) -> Remplissage:
    """Ordre a cours limite : rempli seulement si le marche est venu nous chercher.

    transactions : liste de dicts {price, size, side} observes sur la fenetre
                   qui suit la pose de l'ordre.

    Un achat pose a 0,40 n'est servi que par des transactions a 0,40 ou moins.
    On ne s'attribue qu'une fraction du volume : d'autres ordres etaient devant.
    """
    if montant_vise <= 0:
        return Remplissage(motif="montant nul")

    volume_eligible = 0.0
    for t in transactions or []:
        try:
            prix = float(t.get("price"))
            taille = float(t.get("size"))
        except (TypeError, ValueError):
            continue
        if taille <= 0:
            continue
        if cote == "achat" and prix <= prix_limite + 1e-9:
            volume_eligible += taille
        elif cote == "vente" and prix >= prix_limite - 1e-9:
            volume_eligible += taille

    if volume_eligible <= 0:
        return Remplissage(motif="aucune transaction au prix limite")

    parts_possibles = volume_eligible * participation
    parts_voulues = montant_vise / prix_limite
    parts = min(parts_possibles, parts_voulues)

    if parts < PARTS_MINIMALES:
        return Remplissage(motif="flux trop faible (%.2f parts eligibles)" % parts)

    cout = parts * prix_limite
    f = mf.frais_apporteur(parts, prix_limite, categorie)  # nul par construction
    net = -(cout + f) if cote == "achat" else (cout - f)
    return Remplissage(parts, prix_limite, f, cout, net, 1,
                       borne=parts_possibles < parts_voulues)


def meilleure_prise_de_position(carnet_oui: Carnet, carnet_non: Carnet,
                                sens: str, montant: float, categorie: str):
    """Compare les deux routes possibles pour la meme exposition economique.

    Prendre une position longue sur OUI se fait soit en achetant du OUI, soit
    en vendant du NON. Le gain final est identique, le cout d'execution non.
    Un operateur reel compare toujours les deux ; s'en priver reviendrait a
    sous-estimer l'avantage disponible.

    Renvoie (remplissage, route) ou route vaut 'oui' ou 'non'.
    """
    if sens == "long_oui":
        route_a = executer_preneur(carnet_oui, "achat", montant, categorie)
        route_b = executer_preneur(carnet_non, "vente", montant, categorie)
    elif sens == "court_oui":
        route_a = executer_preneur(carnet_oui, "vente", montant, categorie)
        route_b = executer_preneur(carnet_non, "achat", montant, categorie)
    else:
        raise ValueError("sens inconnu : %s" % sens)

    candidats = []
    if route_a.rempli:
        candidats.append((route_a, "oui"))
    if route_b.rempli:
        candidats.append((route_b, "non"))
    if not candidats:
        return route_a if route_a.motif else route_b, None

    # On classe par cout effectif par part d'exposition obtenue.
    def cout_par_part(paire):
        r = paire[0]
        return abs(r.montant_net) / r.parts if r.parts else float("inf")

    achat = sens == "long_oui"
    candidats.sort(key=cout_par_part, reverse=not achat)
    return candidats[0]


def taille_kelly(avantage: float, prix: float, capital: float,
                 fraction: float = 0.25, plafond_part: float = 0.02) -> float:
    """Dimensionnement par critere de Kelly fractionne.

    avantage : probabilite estimee moins prix de marche, en points
    prix     : prix d'entree
    Le plafond a 2 % du capital prime toujours sur la formule : Kelly suppose
    une probabilite connue, ce qui n'est jamais notre cas.
    """
    p = min(max(float(prix), 1e-6), 1.0 - 1e-6)
    proba = p + avantage
    if proba <= p or proba >= 1.0:
        return 0.0

    # Pari a cote b = (1 - p) / p pour 1 mise. Kelly = (b.q - (1-q)) / b
    b = (1.0 - p) / p
    q = proba
    kelly = (b * q - (1.0 - q)) / b
    if kelly <= 0:
        return 0.0

    montant = capital * kelly * fraction
    return max(0.0, min(montant, capital * plafond_part))
