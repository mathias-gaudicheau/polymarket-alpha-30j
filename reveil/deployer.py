"""Deploiement du reveil sur Cloudflare, via l'API REST.

Pas de wrangler : trois appels HTTP suffisent, et une dependance de moins est
une panne de moins sur trente jours.

  1. televerser le script
  2. poser le declencheur horaire
  3. deposer le jeton GitHub dans les secrets du worker

Le jeton n'est jamais affiche ni ecrit sur disque : il est lu du coffre vers
une variable et transmis directement.

Usage : python reveil/deployer.py
Variables attendues : CF_API_TOKEN, CF_COMPTE_ID, JETON_GITHUB
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from outils.commun import force_utf8  # noqa: E402

NOM = "reveil-polymarket"
CADENCE = "*/15 * * * *"
API = "https://api.cloudflare.com/client/v4"


def appeler(methode, chemin, jeton, corps=None, entetes=None, brut=False):
    url = API + chemin
    tetes = {"Authorization": "Bearer " + jeton, "Accept": "application/json"}
    if entetes:
        tetes.update(entetes)
    charge = None
    if corps is not None:
        if brut:
            charge = corps
        else:
            charge = json.dumps(corps).encode("utf-8")
            tetes["Content-Type"] = "application/json"
    requete = urllib.request.Request(url, data=charge, headers=tetes, method=methode)
    try:
        with urllib.request.urlopen(requete, timeout=60) as rep:
            return json.loads(rep.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        try:
            return json.loads(detail)
        except ValueError:
            return {"success": False, "errors": [{"message": detail[:400]}]}


def televerser(jeton_cf, compte, script):
    """Envoie le script en module ES, format multipart impose par l'API."""
    limite = "----reveil" + "0" * 12
    metadonnees = json.dumps({
        "main_module": "worker.js",
        "compatibility_date": "2025-01-01",
    })
    morceaux = []
    morceaux.append(
        ('--%s\r\nContent-Disposition: form-data; name="metadata"\r\n'
         'Content-Type: application/json\r\n\r\n%s\r\n' % (limite, metadonnees))
        .encode("utf-8"))
    morceaux.append(
        ('--%s\r\nContent-Disposition: form-data; name="worker.js"; '
         'filename="worker.js"\r\nContent-Type: application/javascript+module\r\n\r\n'
         % limite).encode("utf-8"))
    morceaux.append(script.encode("utf-8"))
    morceaux.append(("\r\n--%s--\r\n" % limite).encode("utf-8"))
    corps = b"".join(morceaux)

    return appeler("PUT", "/accounts/%s/workers/scripts/%s" % (compte, NOM),
                   jeton_cf, corps=corps, brut=True,
                   entetes={"Content-Type": "multipart/form-data; boundary=" + limite})


def main():
    force_utf8()
    jeton_cf = os.environ.get("CF_API_TOKEN")
    compte = os.environ.get("CF_COMPTE_ID")
    jeton_gh = os.environ.get("JETON_GITHUB")

    if not jeton_cf or not compte:
        print("ECHEC : CF_API_TOKEN et CF_COMPTE_ID sont requis")
        return 2

    chemin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker.js")
    with open(chemin, "r", encoding="utf-8") as f:
        script = f.read()

    print("1. televersement du script (%d octets)" % len(script))
    r = televerser(jeton_cf, compte, script)
    if not r.get("success"):
        print("   ECHEC : %s" % json.dumps(r.get("errors"))[:400])
        return 1
    print("   OK, worker %s en place" % NOM)

    print("2. declencheur horaire (%s)" % CADENCE)
    r = appeler("PUT", "/accounts/%s/workers/scripts/%s/schedules" % (compte, NOM),
                jeton_cf, corps=[{"cron": CADENCE}])
    if not r.get("success"):
        print("   ECHEC : %s" % json.dumps(r.get("errors"))[:400])
        return 1
    print("   OK, reveil toutes les quinze minutes")

    if jeton_gh:
        print("3. depot du jeton GitHub dans les secrets")
        r = appeler("PUT", "/accounts/%s/workers/scripts/%s/secrets" % (compte, NOM),
                    jeton_cf, corps={"name": "JETON_GITHUB", "text": jeton_gh,
                                     "type": "secret_text"})
        if not r.get("success"):
            print("   ECHEC : %s" % json.dumps(r.get("errors"))[:400])
            return 1
        print("   OK, jeton depose (jamais affiche)")
    else:
        print("3. jeton GitHub absent de l'environnement : etape sautee")
        print("   le reveil sonnera dans le vide tant qu'il n'est pas depose")

    print("\n4. activation du point d'entree manuel")
    r = appeler("POST", "/accounts/%s/workers/scripts/%s/subdomain" % (compte, NOM),
                jeton_cf, corps={"enabled": True})
    if r.get("success"):
        print("   OK, /sante et /declencher accessibles")
    else:
        print("   non active (sans consequence, le declencheur horaire suffit)")

    print("\nDeploiement termine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
