/**
 * Reveil de la collecte.
 *
 * Pourquoi ce composant existe. La planification native de GitHub ne demarre
 * pas sur ce depot : le compte a deux semaines, et GitHub bride les taches
 * planifiees des comptes recents pour empecher l'usage de ses runners comme
 * ferme de calcul. Les declenchements manuels passent, les planifies non.
 *
 * Ce worker se contente donc de faire ce que GitHub refuse : appeler l'API
 * toutes les quinze minutes pour lancer la collecte. Il ne collecte rien
 * lui-meme, ne stocke rien, et ne connait aucune donnee de marche. C'est une
 * sonnerie, pas un cerveau -- ce qui le rend simple a verifier et sans
 * consequence s'il tombe en panne : les cycles reprennent des qu'il repart.
 *
 * Le jeton GitHub vit dans les secrets du worker, jamais dans ce fichier ni
 * dans le depot.
 */

const DEPOT = "mathias-gaudicheau/polymarket-alpha-30j";
const BRANCHE = "main";

// Heures UTC auxquelles le bilan quotidien est reclame en plus de la collecte.
const HEURES_DU_BILAN = [3, 9, 15, 21];

async function declencher(env, fichier) {
  const url =
    `https://api.github.com/repos/${DEPOT}/actions/workflows/${fichier}/dispatches`;
  const reponse = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.JETON_GITHUB}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "reveil-polymarket",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: BRANCHE }),
  });

  // GitHub repond 204 sans corps quand tout va bien.
  if (reponse.status === 204) return { fichier, ok: true, statut: 204 };

  const detail = await reponse.text();
  return {
    fichier,
    ok: false,
    statut: reponse.status,
    detail: detail.slice(0, 300),
  };
}

export default {
  async scheduled(evenement, env, ctx) {
    const resultats = [];
    resultats.push(await declencher(env, "collecte.yml"));

    const instant = new Date(evenement.scheduledTime);
    // Le bilan ne doit partir qu'une fois par creneau horaire, pas a chacun
    // des quatre reveils de l'heure : on ne retient que le premier quart.
    if (HEURES_DU_BILAN.includes(instant.getUTCHours()) &&
        instant.getUTCMinutes() < 15) {
      resultats.push(await declencher(env, "quotidien.yml"));
    }

    for (const r of resultats) {
      if (r.ok) {
        console.log(`declenche ${r.fichier}`);
      } else {
        console.error(`echec ${r.fichier} : ${r.statut} ${r.detail}`);
      }
    }
  },

  // Point d'entree manuel, pour verifier que le reveil est vivant et que son
  // jeton fonctionne encore -- une expiration silencieuse arreterait toute
  // l'experience sans prevenir.
  async fetch(requete, env, ctx) {
    const chemin = new URL(requete.url).pathname;

    if (chemin === "/sante") {
      const test = await fetch(
        `https://api.github.com/repos/${DEPOT}/actions/workflows`,
        {
          headers: {
            Authorization: `Bearer ${env.JETON_GITHUB}`,
            Accept: "application/vnd.github+json",
            "User-Agent": "reveil-polymarket",
          },
        }
      );
      return Response.json({
        reveil: "vivant",
        jeton_valide: test.ok,
        statut_github: test.status,
        depot: DEPOT,
      }, { status: test.ok ? 200 : 503 });
    }

    if (chemin === "/declencher") {
      const r = await declencher(env, "collecte.yml");
      return Response.json(r, { status: r.ok ? 200 : 502 });
    }

    return new Response(
      "Reveil de la collecte Polymarket.\n" +
      "  /sante      etat du reveil et validite du jeton\n" +
      "  /declencher lance un cycle immediatement\n",
      { headers: { "Content-Type": "text/plain; charset=utf-8" } }
    );
  },
};
