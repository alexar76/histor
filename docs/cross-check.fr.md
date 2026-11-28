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
| MCP Transparency Log (yassinht/mcp-transparency-log) | empreintes d'ensembles d'outils, 1 000 serveurs par jour | chaque jour, 12:00 UTC | `/datasets/peers/mcp-transparency-log/` | 2026-11-27 |
| heldfast (rufat325/heldfast-feed) | empreintes par outil, 1 000 serveurs par jour | chaque jour, 12:00 UTC | `/datasets/peers/heldfast/` | 2026-11-27 |

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

Une source qui liste tout le registre, et pas seulement ses changements, est comparée par parts quotidiennes. Avec
`"max_rows": 1000` dans son entrée, un fichier de N lignes est divisé en ceil(1,1 × N / 1000) parts selon le SHA-256 de
`name`, d'un saut de ligne et de `endpoint` (les huit premiers octets, big-endian, modulo le nombre de parts), et
chaque jour compare la part dont le numéro est l'ordinal du jour modulo le nombre de parts. Chaque serveur revient
une fois sur ce nombre de jours ; le rapport indique la part comparée et chacun peut recalculer les lignes qui la
composent. Chaque ligne comparée est une requête à l'API de HISTOR, d'où la part bornée.

## Méthode : empreintes d'ensembles d'outils

C'est l'empreinte d'ensemble d'outils de MTL/1 ([labels.fr.md](labels.fr.md)). Pour chaque outil : `name`,
`description` (`""` si absente), `inputSchema` (`{}` si absent) et `outputSchema` seulement si le serveur a envoyé un
objet ; outils triés par nom en unités de code UTF-16 ; JSON canonique selon la RFC 8785 ; SHA-256 écrit sous forme
de chaîne SRI. Dans sa spécification de lockfile, CrawlCheck l'appelle `jcs_v1.tools_sha256`
(https://crawlcheck.io/spec/mcp-lockfile). Vérifié le 7 novembre 2026 : les deux vecteurs de test de CrawlCheck, six
ensembles tirés des enregistrements de HISTOR et calculés par le service de CrawlCheck, ainsi que le propre serveur de
CrawlCheck donnent des empreintes égales des deux côtés.

Les deux services incluent `annotations`, `title` et `icons` dans une empreinte distincte pour les extras. Le 7 novembre
2026, [CrawlCheck a signalé](https://github.com/modelcontextprotocol/registry/issues/82) 30 concordances, dont
`sha256-G44DGgW7…` pour crawlcheck.io/mcp. Ce résultat porte sur ces lectures. La comparaison des extras dépend de
l’encodage et des limites de stockage. Le rapprochement quotidien compare uniquement l’empreinte d’ensemble d’outils,
car aucune des deux sources ne contient d’empreinte des extras.

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

## Méthode : empreintes par outil

heldfast (https://github.com/rufat325/heldfast) épingle chaque outil séparément plutôt que la liste entière. Son
condensé d'outil ([LOCK.md](https://github.com/rufat325/heldfast/blob/main/docs/LOCK.md)) est le SHA-256 du RFC 8785
de `name`, `title`, `description`, `input_schema`, `annotations`, `output_schema` (omis s'il est vide) et `icons`
(omis s'il est vide). Son flux public, https://github.com/rufat325/heldfast-feed, contient un fichier par serveur.
Pour un serveur hébergé, c'est `state/remote__<nom dans le registre>.json` : le point d'accès (`url`), l'heure de la
lecture qui a produit la liste (`published`), l'empreinte de chaque outil (`tools`) et, si une lecture ultérieure a
échoué, `attempted`. HISTOR calcule les mêmes empreintes à partir de ce qu'il a stocké, titres et annotations compris
(`/api/v1/servers/<id>/fingerprints?format=heldfast`) : la comparaison se fait outil par outil, sans conversion d'un
côté ni de l'autre (`histor/fingerprint_xcheck.py`).

Chaque exécution liste le dossier au commit que désigne `main` à ce moment et garde ce commit dans le rapport. Il y a
environ 44 000 fichiers ; avec `"max_rows": 1000`, chaque jour compare une part choisie par le SHA-256 du nom de
fichier selon la règle ci-dessus : chaque serveur revient environ tous les 49 jours. Le dossier de chaque jour contient
les fichiers tels que lus (`state.input.jsonl`), la comparaison (`state.report.json`, `state.report.md`) et
`SHA256SUMS`.

La route donne l'ensemble d'outils actuel de HISTOR : inchangé depuis `unchangedSince`, lu pour la dernière fois à
`lastObserved`. Les titres et les annotations peuvent changer alors que l'ensemble reste le même ; pour un serveur dont
les empreintes diffèrent, son enregistrement (`/api/v1/servers/<id>`) est donc relu une fois pour connaître son dernier
changement de titres ou d'annotations.

| Verdict | Signification |
|---|---|
| `agree` | toutes les empreintes calculées des deux côtés sont égales, et les deux listent les mêmes outils |
| `disagree` | différentes, alors que HISTOR a lu cet ensemble, titres et annotations compris, avant et après la lecture de heldfast |
| `changed_after` | différentes, et l'ensemble actuel de HISTOR ou ses titres et annotations datent d'après la lecture de heldfast : le serveur a changé entre-temps |
| `read_after` | différentes, et heldfast a lu après la dernière lecture réussie de HISTOR : le serveur a pu changer depuis |
| `histor_no_extras` | HISTOR a lu cet ensemble avant de conserver titres et annotations ; il ne peut pas calculer l'empreinte |
| `histor_no_toolset` | HISTOR liste le serveur mais n'a pas d'ensemble d'outils pour lui |
| `not_listed` | HISTOR ne liste pas ce nom et ce point d'accès |
| `theirs_no_tools` | le fichier de heldfast ne liste aucun outil ; son `attempted.why` (par exemple HTTP 401) est conservé |
| `bad_row` | un champ manque, ou le fichier n'a pas pu être lu |

Les outils dont HISTOR n'a pas pu conserver les titres ou les annotations (au-delà de ses limites de taille) sont
exclus des deux côtés et listés.

Premiers résultats publiés, 2026-11-27 (flux au commit `0b36265b`, part 48 sur 49, 897 serveurs) : 427 `agree` (5 678 outils), 0 `disagree`, 57 `changed_after`, 37 `read_after`, 8 `histor_no_extras`, 21 `histor_no_toolset`, 10 `not_listed`, 337 `theirs_no_tools` (dont 262 HTTP 401). Dans 33 des 37 lignes `read_after`, le robots.txt du serveur interdit le point d'accès : HISTOR ne le lit plus depuis qu'il respecte robots.txt (RFC 9309), le 2026-11-02, alors que heldfast le lit encore.

## Méthode : changements par champ

ScraperWatch publie un journal quotidien des changements d'outils sur un panel fixe de serveurs distants, sous forme
de chemins JSON avec anciennes et nouvelles valeurs. Chaque jour, HISTOR recoupe le jour le plus récent de ce journal dans les deux sens : chaque changement de leur journal contre les enregistrements de HISTOR chemin par chemin,
et chaque changement de HISTOR sur leur panel absent de leur journal, avec sa raison (`histor/peer_xcheck.py`,
[operations.fr.md](operations.fr.md)). Il lit la base de HISTOR, il tourne donc dans son propre conteneur avec les réglages de la base. Ses paramètres
(adresses du journal et du panel, agent robots.txt, heure et première exécution) sont dans son entrée de la liste.
Jusqu'au 7 novembre 2026, il tournait chaque semaine, après l'export du jeu de données.

## Témoins

Sept témoins cosignent les checkpoints de HISTOR (c2sp.org/tlog-witness, cosignature/v1). HISTOR leur envoie son
checkpoint toutes les 15 minutes, soit 96 requêtes par jour, sous les 144 par jour (`qpd`) déclarées pour le journal ;
un témoin qui répond 429 est laissé tranquille le temps indiqué par son `Retry-After`, ou six heures. Le journal figure dans la liste staging de la witness network
`log-list-10qps-4klogs.1` (witness-network.org n'a pas encore de liste de production) ; six des témoins le prennent
dans cette liste et CrawlCheck l'a configuré directement. Mullvad, Markovian et TrustFabric cosignent depuis le
2026-11-04, CrawlCheck depuis le 2026-11-07, GDB Security, Geomys et IPng depuis le 2026-11-19.

| Témoin | Opérateur | vkey |
|---|---|---|
| `witness.stagemole.eu` | Mullvad VPN AB | `witness.stagemole.eu+67f7aea0+BEqSG3yu9YrmcM3BHvQYTxwFj3uSWakQepafafpUqklv` |
| `markovianprotocol.com/witness` | Markovian Protocol | `markovianprotocol.com/witness+41b8827f+BJKLl8rBYM0oUxSaWh9rjTldTprEpYj/SWVIGgacA9XC` |
| `staging.witness.transparency.goog/ring-any-bells` | TrustFabric | `staging.witness.transparency.goog/ring-any-bells+2e1a8dc9+BG5JTpLc3FJtwzgh1Uv+Qelz9qeOH2bfWjS1s0s+y4rL` |
| `crawlcheck.io/witness` | CrawlCheck | `crawlcheck.io/witness+f6b056e2+BCYE6WcaJVekqxEETT9IF/pKvxP7yZM6dLW65YDWpQpE` |
| `witness.gdbsecurity.com` | GDB Security | `witness.gdbsecurity.com+a1398f73+BAbIijGonVIk3ikDac+KKlSm1ELHOzcddhjjB6zB4BNf` |
| `witness.navigli.sunlight.geomys.org` | Geomys | `witness.navigli.sunlight.geomys.org+a3e00fe2+BNy/co4C1Hn1p+INwJrfUlgz7W55dSZReusH/GhUhJ/G` |
| `krabbel.ct.ipng.ch` | IPng Networks GmbH | `krabbel.ct.ipng.ch+4b10dbd8+BO4sLOtnhktlqMCejDx9C5LDd+/xZxbmBb6BP9ZD2Def` |

Le checkpoint le plus récent, https://histor.modelmarket.dev/log/checkpoint, ne porte que la signature du journal,
car les témoins n'ont pas encore vu cette taille. Un checkpoint cosigné par les témoins est servi avec leurs lignes à
`/api/v1/log/checkpoint/<taille>`, par exemple https://histor.modelmarket.dev/api/v1/log/checkpoint/2836615 (signature du journal et sept cosignatures). Le plus récent de ces checkpoints est toujours à https://histor.modelmarket.dev/api/v1/log/checkpoint/cosigned (sa taille dans l'en-tête `Histor-Tree-Size`) : prouvez une entrée par rapport à cette taille. L'agent HESTIA `histor.witnessed@v1` le fait pour un ensemble d'outils. Chaque ligne se
vérifie avec la vkey du témoin indiquée dans le tableau. TrustFabric, Geomys et IPng ont aussi des clés ML-DSA ; HISTOR ne
conserve que les cosignatures Ed25519.
