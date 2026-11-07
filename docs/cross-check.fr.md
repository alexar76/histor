# Recoupement avec un autre observateur

[English](cross-check.md) · [Русский](cross-check.ru.md) · [Español](cross-check.es.md) · [Français](cross-check.fr.md) · [中文](cross-check.zh.md)

HISTOR n'est pas le seul service à lire les listes d'outils des serveurs du MCP Registry. CrawlCheck (crawlcheck.io)
les lit aussi et, depuis le 7 novembre 2026, publie la même empreinte (digest) d'ensemble d'outils que HISTOR. Deux
observateurs qui lisent un serveur à peu près au même moment et calculent l'empreinte indépendamment se vérifient
l'un l'autre : un désaccord signifie que le serveur a fourni autre chose à l'un d'eux (par exemple, il répond
différemment selon le client ou la région), ou que l'un d'eux a un bogue.

## L'empreinte

C'est l'empreinte d'ensemble d'outils de MTL/1 ([labels.fr.md](labels.fr.md)). Pour chaque outil : `name`,
`description` (`""` si absente), `inputSchema` (`{}` si absent) et `outputSchema` seulement si le serveur a envoyé un
objet ; outils triés par nom en unités de code UTF-16 ; JSON canonique selon la RFC 8785 ; SHA-256 écrit sous forme
de chaîne SRI. Dans sa spécification de lockfile, CrawlCheck l'appelle `jcs_v1.tools_sha256`
(https://crawlcheck.io/spec/mcp-lockfile).

Vérifié le 7 novembre 2026 : les deux vecteurs de test publiés par CrawlCheck, six ensembles d'outils tirés des
enregistrements de HISTOR et calculés par le service de CrawlCheck, ainsi que le propre serveur de CrawlCheck donnent
des empreintes égales des deux côtés. `annotations`, `title` et `icons` sont hors de cette empreinte. Les deux
services les conservent sous une seconde empreinte, construite différemment : ces deux-là ne concordent pas et ne
sont pas comparées.

## L'exécution quotidienne

Chaque jour à 12:00 UTC, HISTOR lit le flux public de changements de CrawlCheck
(https://crawlcheck.io/api/v1/mcp/changes, ses 500 événements les plus récents, environ deux jours et demi) et
compare chaque événement qui porte l'empreinte avec l'historique de HISTOR pour ce serveur (`/api/v1/servers/<id>`).
Le résultat est publié sur https://histor.modelmarket.dev/datasets/xcheck-crawlcheck/ dans un dossier par jour : le
flux tel qu'il a été lu (`feed.input.json`), la comparaison (`feed.report.json`, `feed.report.md`) et `SHA256SUMS`.
`latest/` pointe vers le jour le plus récent. Les désaccords sont publiés tels quels, sans filtre.

| Verdict | Signification |
|---|---|
| `agree` | l'empreinte de HISTOR au moment de l'autre lecture est la même (entre deux lectures de HISTOR, l'une ou l'autre compte) |
| `agree_nearby` | ce n'est pas celle qu'avait HISTOR à ce moment, mais HISTOR l'a vue dans les 24 heures : le serveur a changé entre les lectures |
| `disagree` | HISTOR avait des empreintes dans les 24 heures et jamais celle-ci |
| `histor_no_digest` | HISTOR n'a pas d'empreinte pour le serveur dans les 24 heures (pas lu, ou une liste que MTL/1 ne peut pas condenser) |
| `not_listed` | HISTOR ne connaît pas ce nom et cette adresse |
| `bad_row` | un champ manque ou est illisible |

La fenêtre de 24 heures est nécessaire car certains serveurs changent leur liste à chaque lecture : le 7 novembre,
quatre des sept serveurs essayés obtenaient une nouvelle empreinte environ toutes les 30 minutes. Deux observateurs
ne concordent alors que « de près ».

## Le lancer soi-même

```bash
python3 scripts/xcheck_digests.py https://crawlcheck.io/api/v1/mcp/changes --out report.json --md report.md
```

Python 3.11 ou plus récent, bibliothèque standard uniquement. L'entrée est un fichier ou une URL https : le flux de
changements de CrawlCheck tel qu'il est publié, ou toute liste de lignes avec `name` (le nom dans le registre),
`endpoint`, `digest` (SRI ou hex) et `read_at` (RFC 3339) en JSON, JSON Lines ou CSV. Code de sortie 0 : aucun
`disagree` ; 1 : au moins un ; 2 : entrée illisible.

## Premier résultat

7 novembre 2026, première exécution sur le flux : 8 événements avec l'empreinte, 7 `agree`, 1 `histor_no_digest`,
0 `disagree`.
