"""Acces a la base Neon en SQL sur HTTP.

Neon expose un point d'acces /sql qui accepte une requete parametree et
renvoie du JSON. On l'utilise plutot qu'un pilote Postgres classique pour une
raison operationnelle : le collecteur tourne 288 fois par jour sur GitHub
Actions, et une installation de dependance a chaque execution serait 288
occasions de panne pour un gain nul. Ici, bibliotheque standard uniquement.

Les parametres suivent la convention Postgres ($1, $2...), jamais la
concatenation de chaines.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

_MOTIF_URL = re.compile(r"^postgres(?:ql)?://([^:]+):([^@]+)@([^/?]+)/([^?]+)")


class ErreurBdd(RuntimeError):
    pass


class Bdd:
    """Connexion logique a une base Neon. Sans etat, chaque appel est autonome."""

    def __init__(self, url=None, timeout=30):
        self.url = url or os.environ.get("DATABASE_URL") or _lire_env_local()
        if not self.url:
            raise ErreurBdd(
                "DATABASE_URL introuvable : ni variable d'environnement, ni .env local")
        m = _MOTIF_URL.match(self.url)
        if not m:
            raise ErreurBdd("DATABASE_URL de forme inattendue")
        hote = m.group(3)
        # Le point d'acces SQL vit sur l'hote direct, sans le suffixe de pool.
        self.hote = hote.replace("-pooler", "")
        self.point = "https://%s/sql" % self.hote
        self.timeout = timeout

    # -- coeur ------------------------------------------------------------

    def executer(self, sql, params=None):
        """Execute une requete et renvoie la liste des lignes (dictionnaires)."""
        corps = json.dumps({"query": sql, "params": list(params or [])}).encode("utf-8")
        requete = urllib.request.Request(self.point, data=corps, method="POST", headers={
            "Content-Type": "application/json",
            "Neon-Connection-String": self.url,
            "Neon-Raw-Text-Output": "false",
            "Neon-Array-Mode": "false",
        })
        try:
            with urllib.request.urlopen(requete, timeout=self.timeout) as rep:
                charge = json.loads(rep.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            detail = ""
            try:
                detail = err.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                pass
            raise ErreurBdd("HTTP %s sur la base : %s" % (err.code, detail)) from err
        except Exception as err:
            raise ErreurBdd("acces base impossible : %s" % err) from err

        if isinstance(charge, dict):
            if "rows" in charge:
                return charge["rows"]
            if "message" in charge:
                raise ErreurBdd("erreur SQL : %s" % charge["message"])
        return []

    def executer_script(self, sql_multiple):
        """Execute plusieurs instructions separees par des points-virgules.

        Le point d'acces HTTP n'accepte qu'une instruction a la fois : on les
        decoupe. Le decoupage respecte les corps de fonction delimites par $$.
        """
        resultats = []
        for instruction in _decouper(sql_multiple):
            if instruction.strip():
                resultats.append(self.executer(instruction))
        return resultats

    def valeur(self, sql, params=None, defaut=None):
        """Premiere colonne de la premiere ligne, ou le defaut."""
        lignes = self.executer(sql, params)
        if not lignes:
            return defaut
        premiere = lignes[0]
        if isinstance(premiere, dict):
            return next(iter(premiere.values()), defaut)
        return premiere[0] if premiere else defaut

    def inserer_en_masse(self, table, colonnes, lignes, sur_conflit=None,
                         taille_lot=500):
        """Insere beaucoup de lignes en peu d'aller-retours.

        Construit une seule instruction VALUES par lot. Les valeurs passent
        toujours par des parametres numerotes, jamais par interpolation.
        """
        if not lignes:
            return 0
        total = 0
        for depart in range(0, len(lignes), taille_lot):
            lot = lignes[depart:depart + taille_lot]
            params, morceaux = [], []
            for ligne in lot:
                reperes = []
                for valeur in ligne:
                    params.append(valeur)
                    reperes.append("$%d" % len(params))
                morceaux.append("(%s)" % ", ".join(reperes))
            sql = "INSERT INTO %s (%s) VALUES %s" % (
                table, ", ".join(colonnes), ", ".join(morceaux))
            if sur_conflit:
                sql += " " + sur_conflit
            self.executer(sql, params)
            total += len(lot)
        return total

    def test(self):
        """Verifie que la base repond et renvoie sa version."""
        return self.valeur("SELECT version()")


def _lire_env_local():
    """Recupere DATABASE_URL depuis un .env a la racine du projet."""
    racine = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    chemin = os.path.join(racine, ".env")
    if not os.path.exists(chemin):
        return None
    with open(chemin, "r", encoding="utf-8") as f:
        for ligne in f:
            ligne = ligne.strip()
            if ligne.startswith("DATABASE_URL="):
                return ligne.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _decouper(sql):
    """Decoupe un script SQL en instructions, en respectant les blocs $$."""
    instructions, courante, dans_bloc = [], [], False
    for ligne in sql.splitlines():
        nue = ligne.strip()
        if nue.startswith("--"):
            continue
        if "$$" in ligne:
            dans_bloc = not dans_bloc if ligne.count("$$") % 2 else dans_bloc
        courante.append(ligne)
        if not dans_bloc and nue.endswith(";"):
            instructions.append("\n".join(courante))
            courante = []
    if courante:
        instructions.append("\n".join(courante))
    return instructions
