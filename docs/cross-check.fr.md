# Recoupements avec d'autres observateurs

[English](cross-check.md) · [Русский](cross-check.ru.md) · [Español](cross-check.es.md) · [Français](cross-check.fr.md) · [中文](cross-check.zh.md)

HISTOR n'est pas le seul service à lire les listes d'outils des serveurs du MCP Registry. Des observateurs qui lisent
les mêmes serveurs indépendamment se vérifient l'un l'autre : quand ils divergent, un serveur leur a répondu
différemment (selon le client, la région ou le moment), ou l'un d'eux a un bogue. HISTOR recoupe ses enregistrements
avec ces observateurs selon un calendrier et publie chaque recoupement, désaccords compris, sous
https://histor.modelmarket.dev/datasets/peers/.

## Les observateurs

| Observateur | Méthode | Calendrier | Publié sous | Depuis |
|---|---|---|---|---|
| CrawlCheck (crawlcheck.io) | empreintes (digests) d'ensembles d'outils | chaque jour, 12:00 UTC | `/datasets/peers/crawlcheck/` | 2026-11-07 |
| ScraperWatch (scraperwatch.com) | changements par champ | chaque jour, 12:00 UTC | `/datasets/peers/scraperwatch/` | 2026-11-01 |

La liste est un seul fichier de configuration, `/etc/histor/peers.json` sur l'hôte (`deploy/peers.json` dans ce
dépôt). Sa forme publique est https://histor.modelmarket.dev/datasets/peers/index.json : chaque observateur, sa
méthode, ses sources et son calendrier, et le jour le plus récent recoupé. Chaque jour est un dossier,
`/datasets/peers/<observateur>/<AAAA-MM-JJ>/`, et `latest/` pointe vers le plus récent. `/datasets/xcheck-crawlcheck/`,
l'adresse utilisée avant l'existence de la liste, mène au même endroit.

## Ajouter un observateur

Un observateur qui publie des empreintes d'ensembles d'outils MTL/1 (ci-dessous) n'a besoin que d'une entrée dans la
liste :

```json
{
  "slug": "example",
  "name": "Example Observer",
  "contact": "ops@example.org",
  "method": "digest",
  "window_hours": 24,
  "sources": [{"name": "list", "url": "https://example.org/histor-list.jsonl"}]
}
```

Une source est tout fichier que l'observateur publie en https avec une ligne par lecture de serveur : `name` (le nom
dans le registre), `endpoint`, `digest` (SRI ou hex) et `read_at` (RFC 3339), en JSON, JSON Lines ou CSV. Le flux de
changements de CrawlCheck est lu tel qu'il est publié. Une ligne avec `digest` null et un champ `outcome` indique que
l'observateur n'a pas pu lire ce serveur. `histor-peers.timer` exécute chaque jour toutes les entrées
(`deploy/peers_check.sh`, `histor/peers.py`) ; voir [operations.fr.md](operations.fr.md). Un observateur avec un journal de changements par champ au format de ScraperWatch s'ajoute avec la méthode `field-diff` et les paramètres que montre l'entrée de ScraperWatch dans `deploy/peers.json`.

## Méthode : empreintes d'ensembles d'outils

C'est l'empreinte d'ensemble d'outils de MTL/1 ([labels.fr.md](labels.fr.md)). Pour chaque outil : `name`,
`description` (`""` si absente), `inputSchema` (`{}` si absent) et `outputSchema` seulement si le serveur a envoyé un
objet ; outils triés par nom en unités de code UTF-16 ; JSON canonique selon la RFC 8785 ; SHA-256 écrit sous forme
de chaîne SRI. Dans sa spécification de lockfile, CrawlCheck l'appelle `jcs_v1.tools_sha256`
(https://crawlcheck.io/spec/mcp-lockfile). Vérifié le 7 novembre 2026 : les deux vecteurs de test de CrawlCheck, six
ensembles tirés des enregistrements de HISTOR et calculés par le service de CrawlCheck, ainsi que le propre serveur de
CrawlCheck donnent des empreintes égales des deux côtés.

`annotations`, `title` et `icons` sont hors de cette empreinte ; les deux services les conservent sous une seconde
empreinte, celle des extras. Depuis le soir du 7 novembre 2026, CrawlCheck construit son `extras_sha256` comme HISTOR
construit son empreinte des extras (pas selon la RFC 8785), elles concordent donc aussi : pour crawlcheck.io/mcp, les
deux valent `sha256-G44DGgW7…`. L'exécution quotidienne ne compare que l'empreinte d'ensemble d'outils, car les sources
ne portent pas d'empreinte des extras.

Chaque ligne publiée par l'observateur est comparée à l'historique de HISTOR pour ce serveur (`/api/v1/servers/<id>`) :

| Verdict | Signification |
|---|---|
| `agree` | l'empreinte de HISTOR au moment de l'autre lecture est la même (entre deux lectures de HISTOR, l'une ou l'autre compte) |
| `agree_nearby` | ce n'est pas celle qu'avait HISTOR à ce moment, mais HISTOR l'a vue dans la fenêtre : le serveur a changé entre les lectures |
| `disagree` | HISTOR avait des empreintes dans la fenêtre et jamais celle-ci |
| `histor_no_digest` | HISTOR n'a pas d'empreinte pour le serveur dans la fenêtre (pas lu, ou une liste que MTL/1 ne peut pas condenser) |
| `not_listed` | HISTOR ne connaît pas ce nom et cette adresse |
| `theirs_no_digest` | l'autre observateur n'a pas pu lire la liste (`digest` null, `outcome` dit pourquoi) |
| `bad_row` | un champ manque ou est illisible |

La fenêtre, de 24 heures par défaut, est nécessaire car certains serveurs changent leur liste à chaque lecture : le
7 novembre, quatre des sept serveurs essayés obtenaient une nouvelle empreinte environ toutes les 30 minutes. Deux
observateurs ne concordent alors que « de près ».

Pour CrawlCheck, l'exécution lit deux sources : son flux public de changements
(https://crawlcheck.io/api/v1/mcp/changes, les 500 événements les plus récents, environ deux jours et demi, d'où une
exécution quotidienne) et sa liste partagée hebdomadaire de 30 serveurs, relus chaque lundi avant 11:30 UTC
(https://crawlcheck.io/data/xcheck/histor/latest.jsonl, éditions datées avec une déclaration signée à côté). Le dossier
de chaque jour contient, par source, le fichier tel qu'il a été lu (`<source>.input.*`), la comparaison
(`<source>.report.json`, `<source>.report.md`) et `SHA256SUMS`.

Pour lancer le recoupement soi-même, avec Python 3.11 ou plus récent et sans rien installer :

```bash
python3 scripts/xcheck_digests.py https://crawlcheck.io/data/xcheck/histor/latest.jsonl --out report.json --md report.md
```

Code de sortie 0 : aucun `disagree` ; 1 : au moins un ; 2 : entrée illisible.

Premiers résultats, 7 novembre 2026 : première édition de la liste partagée (sha256 `9271b8ac…`), 30 serveurs,
30 `agree` ; flux de changements, 16 lignes avec l'empreinte, 15 `agree`, 1 `histor_no_digest`, 0 `disagree`.

## Méthode : changements par champ

ScraperWatch publie un journal quotidien des changements d'outils sur un panel fixe de serveurs distants, sous forme
de chemins JSON avec anciennes et nouvelles valeurs. Chaque jour, HISTOR recoupe le jour le plus récent de ce journal dans les deux sens : chaque changement de leur journal contre les enregistrements de HISTOR chemin par chemin,
et chaque changement de HISTOR sur leur panel absent de leur journal, avec sa raison (`histor/peer_xcheck.py`,
[operations.fr.md](operations.fr.md)). Il lit la base de HISTOR, il tourne donc dans son propre conteneur avec les réglages de la base. Ses paramètres
(adresses du journal et du panel, agent robots.txt, heure et première exécution) sont dans son entrée de la liste.
Jusqu'au 7 novembre 2026, il tournait chaque semaine, après l'export du jeu de données.

## Un quatrième témoin

Depuis le 7 novembre 2026, CrawlCheck cosigne aussi les checkpoints de HISTOR en tant que témoin
(c2sp.org/tlog-witness), depuis son propre réseau : `crawlcheck.io/witness`, vkey
`crawlcheck.io/witness+f6b056e2+BCYE6WcaJVekqxEETT9IF/pKvxP7yZM6dLW65YDWpQpE`. Sa cosignature figure sur
https://histor.modelmarket.dev/log/checkpoint à côté de celles des trois témoins qui signaient déjà.
