"""Journal des instantanes, ecrit dans le depot lui-meme.

Pourquoi pas la base : le plan gratuit Neon accorde 100 heures de calcul par
mois et la base s'endort apres cinq minutes sans requete. Une ecriture toutes
les cinq minutes la maintiendrait eveillee en continu, soit 180 heures
consommees et un quota epuise vers le seizieme jour.

Pourquoi le depot convient mieux : les detecteurs de la couche 1 ne
travaillent que sur l'instant present, aucun historique n'est requis en
direct. Et un instantane inscrit dans un commit est date et scelle, ce qui
sert directement la valeur de preuve du projet.

Format retenu, pour tenir dans une taille raisonnable :

  AAAA-MM-JJ/HH00-complet.json.gz   une photographie complete par heure
  AAAA-MM-JJ/HHMM-delta.json.gz     seulement ce qui a bouge, toutes les 5 min

Reconstituer l'etat courant se fait en lisant la derniere photographie
complete puis en lui appliquant les deltas posterieurs.
"""

from __future__ import annotations

import gzip
import json
import os
import time

RACINE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "journal")

# Un prix doit bouger d'au moins ce seuil pour meriter une ligne de delta.
# Le pas de cotation vaut 0,001 : on ignore donc le simple frisson d'un tick.
SEUIL_PRIX = 0.002
SEUIL_LIQUIDITE = 0.15   # variation relative

# Champs conserves, dans cet ordre, en tableau plutot qu'en dictionnaire.
CHAMPS = ["id", "achat", "vente", "dernier", "liquidite", "volume24"]


def _dossier_du_jour(horodatage=None):
    t = time.gmtime(horodatage or time.time())
    return os.path.join(RACINE, time.strftime("%Y-%m-%d", t))


def _nom(horodatage, genre):
    t = time.gmtime(horodatage)
    return "%s-%s.json.gz" % (time.strftime("%H%M", t), genre)


def _compacter(marche):
    """Reduit un marche a un tableau de nombres, prix en milliemes."""
    def mil(v):
        return None if v is None else int(round(float(v) * 1000))
    return [
        int(marche["id"]),
        mil(marche.get("achat")),
        mil(marche.get("vente")),
        mil(marche.get("dernier")),
        int(marche.get("liquidite") or 0),
        int(marche.get("volume24") or 0),
    ]


def _ecrire(chemin, charge):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    brut = json.dumps(charge, separators=(",", ":")).encode("utf-8")
    with gzip.open(chemin, "wb", compresslevel=9) as f:
        f.write(brut)
    return len(brut), os.path.getsize(chemin)


def _lire(chemin):
    with gzip.open(chemin, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def etat_courant():
    """Reconstitue le dernier etat connu : (dict id -> ligne compacte, horodatage).

    Renvoie ({}, None) au tout premier cycle.
    """
    if not os.path.isdir(RACINE):
        return {}, None

    jours = sorted(d for d in os.listdir(RACINE)
                   if os.path.isdir(os.path.join(RACINE, d)))
    if not jours:
        return {}, None

    # On remonte au plus loin sur deux jours : suffisant pour retrouver une
    # photographie complete, meme apres une interruption nocturne.
    fichiers = []
    for jour in jours[-2:]:
        dossier = os.path.join(RACINE, jour)
        for nom in sorted(os.listdir(dossier)):
            if nom.endswith(".json.gz"):
                fichiers.append(os.path.join(dossier, nom))

    depart = None
    for i in range(len(fichiers) - 1, -1, -1):
        if fichiers[i].endswith("-complet.json.gz"):
            depart = i
            break
    if depart is None:
        return {}, None

    etat, dernier_t = {}, None
    for chemin in fichiers[depart:]:
        try:
            charge = _lire(chemin)
        except (OSError, ValueError):
            continue
        dernier_t = charge.get("t", dernier_t)
        for ligne in charge.get("m", []):
            etat[ligne[0]] = ligne
    return etat, dernier_t


def _a_bouge(avant, apres):
    """Un marche merite-t-il une ligne de delta ?"""
    if avant is None:
        return True
    for i in (1, 2, 3):   # achat, vente, dernier
        a, b = avant[i], apres[i]
        if a is None and b is None:
            continue
        if a is None or b is None:
            return True
        if abs(a - b) >= SEUIL_PRIX * 1000:
            return True
    for i in (4, 5):      # liquidite, volume
        a, b = avant[i] or 0, apres[i] or 0
        if a == 0 and b == 0:
            continue
        if abs(b - a) >= max(1.0, SEUIL_LIQUIDITE * max(a, 1)):
            return True
    return False


def enregistrer(marches, horodatage=None, forcer_complet=False):
    """Ecrit l'instantane du cycle et renvoie un diagnostic.

    Une photographie complete est ecrite au premier cycle de chaque heure,
    ou si l'etat precedent est introuvable. Sinon, seuls les marches qui ont
    bouge sont consignes.
    """
    t = int(horodatage or time.time())
    lignes = [_compacter(m) for m in marches]

    precedent, _ = etat_courant()
    minute = time.gmtime(t).tm_min
    complet = forcer_complet or not precedent or minute < 5

    if complet:
        charge = {"t": t, "genre": "complet", "n": len(lignes), "m": lignes}
        chemin = os.path.join(_dossier_du_jour(t), _nom(t, "complet"))
    else:
        bouges = [l for l in lignes if _a_bouge(precedent.get(l[0]), l)]
        charge = {"t": t, "genre": "delta", "n": len(bouges),
                  "total_univers": len(lignes), "m": bouges}
        chemin = os.path.join(_dossier_du_jour(t), _nom(t, "delta"))

    brut, compresse = _ecrire(chemin, charge)
    return {
        "chemin": os.path.relpath(chemin, os.path.dirname(RACINE)).replace("\\", "/"),
        "genre": charge["genre"],
        "lignes_ecrites": charge["n"],
        "univers": len(lignes),
        "octets_bruts": brut,
        "octets_compresses": compresse,
    }


def ajouter_signaux(signaux, horodatage=None):
    """Ajoute les signaux du cycle au journal du jour, une ligne par signal."""
    if not signaux:
        return {"nb": 0, "chemin": None}
    t = int(horodatage or time.time())
    chemin = os.path.join(_dossier_du_jour(t), "signaux.jsonl")
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "a", encoding="utf-8") as f:
        for s in signaux:
            enr = s.en_dict() if hasattr(s, "en_dict") else dict(s)
            enr["t"] = t
            f.write(json.dumps(enr, ensure_ascii=False, separators=(",", ":")) + "\n")
    return {"nb": len(signaux),
            "chemin": os.path.relpath(chemin, os.path.dirname(RACINE)).replace("\\", "/")}


def ajouter_diagnostic(diagnostic, horodatage=None):
    """Consigne les temoins de qualite des donnees, meme quand ils sont vides."""
    t = int(horodatage or time.time())
    chemin = os.path.join(_dossier_du_jour(t), "sante.jsonl")
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": t, **diagnostic}, ensure_ascii=False,
                           separators=(",", ":")) + "\n")
    return chemin


def parcourir_instantanes(depuis=None, jusqu_a=None):
    """Itere sur l'historique reconstitue : (horodatage, dict id -> ligne).

    Sert au moteur de decouverte, qui a besoin de l'historique complet alors
    que les detecteurs ne travaillent que sur l'instant present.
    """
    if not os.path.isdir(RACINE):
        return
    etat = {}
    for jour in sorted(os.listdir(RACINE)):
        dossier = os.path.join(RACINE, jour)
        if not os.path.isdir(dossier):
            continue
        for nom in sorted(os.listdir(dossier)):
            if not nom.endswith(".json.gz"):
                continue
            try:
                charge = _lire(os.path.join(dossier, nom))
            except (OSError, ValueError):
                continue
            if charge.get("genre") == "complet":
                etat = {}
            for ligne in charge.get("m", []):
                etat[ligne[0]] = ligne
            t = charge.get("t")
            if depuis and t < depuis:
                continue
            if jusqu_a and t > jusqu_a:
                return
            yield t, dict(etat)


def taille_journal():
    """Poids total du journal, pour surveiller la croissance du depot."""
    total, fichiers = 0, 0
    for racine, _dossiers, noms in os.walk(RACINE):
        for nom in noms:
            total += os.path.getsize(os.path.join(racine, nom))
            fichiers += 1
    return {"octets": total, "mo": round(total / 1048576.0, 2), "fichiers": fichiers}
