"""Applique le schema, de facon idempotente.

Peut etre relance sans risque : tout est en CREATE ... IF NOT EXISTS.
Codes de sortie : 0 succes, 1 echec d'une instruction, 2 base injoignable.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moteur.bdd import Bdd, ErreurBdd  # noqa: E402
from outils.commun import force_utf8  # noqa: E402

CHEMIN_SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

TABLES_ATTENDUES = [
    "marche", "instantane", "instantane_horaire", "carnet", "transaction_marche",
    "reference_externe", "confiance_source", "strategie", "signal", "execution",
    "position", "denouement", "capital", "portefeuille_suivi", "cycle", "alerte",
]


def main():
    force_utf8()
    try:
        base = Bdd()
    except ErreurBdd as err:
        print("ECHEC : %s" % err)
        return 2

    print("base   : %s" % base.hote)
    try:
        print("version: %s" % (base.test() or "")[:50])
    except ErreurBdd as err:
        print("ECHEC : base injoignable -> %s" % err)
        return 2

    with open(CHEMIN_SCHEMA, "r", encoding="utf-8") as f:
        script = f.read()

    echecs = 0
    for i, instruction in enumerate(_instructions(script), 1):
        court = " ".join(instruction.split())[:70]
        try:
            base.executer(instruction)
            print("  %2d. OK   %s" % (i, court))
        except ErreurBdd as err:
            echecs += 1
            print("  %2d. ECHEC %s\n      -> %s" % (i, court, err))

    print("\nVerification des tables :")
    manquantes = []
    for table in TABLES_ATTENDUES:
        present = base.valeur(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = $1", [table])
        etat = "OK  " if str(present) == "1" else "MANQUE"
        if str(present) != "1":
            manquantes.append(table)
        print("  %s %s" % (etat, table))

    if manquantes:
        print("\nECHEC : tables manquantes -> %s" % ", ".join(manquantes))
        return 1
    if echecs:
        print("\n%d instruction(s) en echec mais toutes les tables sont la." % echecs)
    print("\nSchema en place (%d tables)." % len(TABLES_ATTENDUES))
    return 0


def _instructions(script):
    """Decoupe le script en instructions completes."""
    sortie, courante = [], []
    for ligne in script.splitlines():
        nue = ligne.strip()
        if not nue or nue.startswith("--"):
            continue
        courante.append(ligne)
        if nue.endswith(";"):
            sortie.append("\n".join(courante))
            courante = []
    if courante:
        sortie.append("\n".join(courante))
    return sortie


if __name__ == "__main__":
    sys.exit(main())
