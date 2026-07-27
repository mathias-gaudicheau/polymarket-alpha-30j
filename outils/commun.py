"""Utilitaires partages : encodage, HTTP, resume de schema.

Bibliotheque standard uniquement. Aucune sortie avec emoji.
"""

from __future__ import annotations

import gzip
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def force_utf8() -> None:
    """Force stdout/stderr en UTF-8 (Windows utilise cp1252 par defaut)."""
    for flux in (sys.stdout, sys.stderr):
        try:
            flux.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


AGENT = "polymarket-alpha-30j/1.0 (recherche; simulation sans transaction)"


class ReponseHttp:
    """Resultat d'un appel HTTP, succes ou echec."""

    __slots__ = ("url", "statut", "donnees", "erreur", "ms", "entetes")

    def __init__(self, url, statut=None, donnees=None, erreur=None, ms=0.0, entetes=None):
        self.url = url
        self.statut = statut
        self.donnees = donnees
        self.erreur = erreur
        self.ms = ms
        self.entetes = entetes or {}

    @property
    def ok(self) -> bool:
        return self.erreur is None and self.statut is not None and 200 <= self.statut < 300

    def resume(self) -> str:
        if self.ok:
            return "OK %s en %d ms" % (self.statut, self.ms)
        if self.statut is not None:
            return "ECHEC HTTP %s en %d ms : %s" % (self.statut, self.ms, self.erreur)
        return "ECHEC reseau en %d ms : %s" % (self.ms, self.erreur)


def http_json(url, params=None, methode="GET", corps=None, entetes=None, timeout=25,
              essais=3, pause=1.5):
    """Appel HTTP JSON avec reessais sur 429 et erreurs reseau.

    Renvoie toujours une ReponseHttp, ne leve jamais.
    """
    if params:
        propres = {k: v for k, v in params.items() if v is not None}
        if propres:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(propres)

    charge = None
    tetes = {"User-Agent": AGENT, "Accept": "application/json"}
    if corps is not None:
        charge = json.dumps(corps).encode("utf-8")
        tetes["Content-Type"] = "application/json"
    if entetes:
        tetes.update(entetes)

    derniere = None
    for tentative in range(essais):
        debut = time.time()
        try:
            requete = urllib.request.Request(url, data=charge, headers=tetes, method=methode)
            with urllib.request.urlopen(requete, timeout=timeout) as rep:
                brut = rep.read()
                if rep.headers.get("Content-Encoding") == "gzip":
                    brut = gzip.decompress(brut)
                ms = (time.time() - debut) * 1000
                texte = brut.decode("utf-8", errors="replace")
                try:
                    donnees = json.loads(texte) if texte.strip() else None
                except json.JSONDecodeError:
                    return ReponseHttp(url, rep.status, None,
                                       "reponse non-JSON : %s" % texte[:200], ms,
                                       dict(rep.headers))
                return ReponseHttp(url, rep.status, donnees, None, ms, dict(rep.headers))
        except urllib.error.HTTPError as err:
            ms = (time.time() - debut) * 1000
            detail = ""
            try:
                detail = err.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            derniere = ReponseHttp(url, err.code, None, detail or str(err.reason), ms,
                                   dict(err.headers or {}))
            # 429 et 5xx meritent un reessai, le reste non.
            if err.code != 429 and err.code < 500:
                return derniere
        except Exception as err:  # noqa: BLE001 - on veut vraiment tout capturer
            ms = (time.time() - debut) * 1000
            derniere = ReponseHttp(url, None, None, "%s: %s" % (type(err).__name__, err), ms)

        if tentative < essais - 1:
            time.sleep(pause * (2 ** tentative))

    return derniere


def schema_de(valeur, profondeur=3, _niveau=0):
    """Resume la forme d'un objet JSON sans en cracher tout le contenu.

    Les listes sont reduites a leur premier element, les chaines tronquees.
    Sert a decouvrir un schema d'API sans noyer les journaux.
    """
    if _niveau >= profondeur:
        return "..."
    if valeur is None:
        return "null"
    if isinstance(valeur, bool):
        return "bool=%s" % valeur
    if isinstance(valeur, int):
        return "int=%s" % valeur
    if isinstance(valeur, float):
        return "float=%s" % round(valeur, 6)
    if isinstance(valeur, str):
        court = valeur if len(valeur) <= 60 else valeur[:57] + "..."
        return "str(%d)=%s" % (len(valeur), court)
    if isinstance(valeur, list):
        if not valeur:
            return "list(0)"
        return {"__liste__": len(valeur), "0": schema_de(valeur[0], profondeur, _niveau + 1)}
    if isinstance(valeur, dict):
        return {c: schema_de(v, profondeur, _niveau + 1) for c, v in list(valeur.items())[:40]}
    return type(valeur).__name__


def maintenant_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
