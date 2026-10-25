# Ce que coûte une liste d’outils

> 🌐 [English](tool-list-cost.md) · [Русский](tool-list-cost.ru.md) · [Español](tool-list-cost.es.md) · **Français** · [中文](tool-list-cost.zh.md)

Chaque serveur MCP auquel un client se connecte énumère ses outils : pour chacun, un nom, une description et un
schéma d’entrée. Le client peut envoyer la liste complète, un sous-ensemble ou charger les tools à la demande ; cette étude compte la représentation complète indiquée. Un lecteur de notre étude de 12 915 paquets (Paul Irolla) a proposé de le compter en tokens. HISTOR
conserve la liste d’outils actuelle de chaque serveur qu’il observe : nous avons compté.

## Les chiffres

Pour le CI de Raju, l’[exemple public autonome](https://dev.to/alexar76/a-copyable-mcp-tool-cost-check-for-ci-with-passing-and-failing-examples-3hl5) vérifie séparément total, descriptions et schémas avec CLI/tokenizer épinglés. Le sens normal retourne 0 ; l’inversion volontaire retourne 1, sans établir une régression ultérieure. Ces estimations ne sont pas des factures : les [limites](measurement-limits.fr.md) couvrent sélection des tools, cache et relevés d’usage réels.

Instantané du 2026-10-19 : chaque paquet (npm, PyPI) et chaque serveur distant du journal ayant au moins un outil,
avec sa liste actuelle. Une liste est comptée telle qu’un client l’envoie à un modèle : pour chaque outil,
`{name, description, input_schema}` en JSON compact. Le tokenizer est `o200k_base` (tiktoken 0.12, la famille
GPT-4o).

| | Paquets | Serveurs distants |
|---|---|---|
| Serveurs | 18 204 | 16 170 |
| Définitions d’outils | 337 449 | 232 427 |
| Outils par serveur : médiane / moyenne / 90e centile | 8 / 18,5 / 40 | 7 / 14,4 / 31 |
| **Tokens par serveur : médiane** | **1 272** | **1 328** |
| moyenne | 3 812 | 3 412 |
| 90e centile | 8 361 | 7 425 |
| 99e centile | 39 612 | 22 294 |
| le plus grand | 304 780 | 547 935 |
| Tokens par outil : médiane / moyenne | 129 / 206 | 231 / 237 |
| Serveurs au-delà de 10 000 tokens | 1 447 | 618 |
| Serveurs au-delà de 100 000 tokens | 31 | 9 |

La moyenne vaut trois fois la médiane : quelques serveurs à des centaines ou des milliers d’outils portent l’essentiel
des tokens. Les « environ 24 outils par serveur » de l’étude étaient la moyenne des 7 656 paquets qui ont répondu le
2026-10-13 ; sur l’ensemble actuel, plus grand, la moyenne est de 18,5 et la médiane de 8.

## En argent

La liste est renvoyée à chaque requête, donc son coût vaut tokens × prix × requêtes. Pour 1 000 requêtes, aux prix
catalogue du 2026-10-19 (OpenRouter) :

| La liste d’un serveur | Tokens | 0,10 $ le million (Claude Haiku 5.5) | 2 $ le million (Claude Sonnet 5.5) | Sonnet 5.5 avec cache, requêtes à moins de 5 minutes d’intervalle | Idem, à plus de 5 minutes |
|---|---|---|---|---|---|
| paquet médian | 1 272 | 0,13 $ | 2,54 $ | 0,13 $ | 3,18 $ |
| paquet moyen | 3 812 | 0,38 $ | 7,62 $ | 0,39 $ | 9,53 $ |
| 90e centile | 8 361 | 0,84 $ | 16,72 $ | 0,86 $ | 20,90 $ |
| 99e centile | 39 612 | 3,96 $ | 79,22 $ | 4,06 $ | 99,03 $ |

Les colonnes avec cache utilisent les prix de cache de Sonnet 5.5 : 2,50 $ le million pour écrire le cache de 5
minutes (1,25 fois le prix d’entrée) et 0,10 $ pour le lire. Le cache garde un préfixe cinq minutes après la dernière
requête qui l’a utilisé : 1 000 requêtes à moins de cinq minutes d’intervalle paient une écriture et 999 lectures ;
plus espacées, elles le réécrivent à chaque fois, un quart de plus que sans cache. Le cache d’une heure couvre des
intervalles jusqu’à une heure, avec des écritures au double du prix d’entrée. Les outils viennent en premier dans le
prompt, avant le prompt système et la conversation : une liste modifiée (un autre ordre, ou une relecture après
`notifications/tools/list_changed`) réécrit tout dans le cache.

Un client avec cinq serveurs médians envoie environ 6 400 tokens de définitions d’outils avant le premier mot de
l’utilisateur. Les plus grandes listes ne tiennent même pas : 304 780 tokens dépassent une fenêtre de contexte de
200 000 tokens.

Deux choses réduisent la facture. Le cache de prompt, quand le client garde la liste d’outils identique et en tête
du prompt : au prix catalogue de Sonnet 5.5, une lecture en cache coûte un vingtième. Et le chargement des
définitions à la demande : le modèle voit d’abord les noms des outils et reçoit un schéma au moment d’appeler l’outil.

## Ce que cela ne mesure pas

- **Un seul tokenizer.** Chaque modèle découpe différemment, donc les comptes d’autres modèles diffèrent,
  généralement de quelques dizaines de pour cent ; ce qui se transpose, c’est la forme : une médiane proche de
  1 300 tokens et une longue traîne.
- **Le texte propre aux fournisseurs.** L’API d’un modèle ajoute ses propres instructions d’usage des outils autour
  des définitions ; il n’est pas compté.
- **Ce que les clients envoient vraiment.** Certains retirent des champs ou raccourcissent les longues descriptions ;
  d’autres envoient aussi le schéma de sortie. Comptée en entier, telle qu’elle est listée, la liste d’un paquet a une
  médiane de 1 390 tokens et un 90e centile de 9 960.
- **Quels serveurs les gens utilisent.** Chaque serveur listé compte une fois : un serveur populaire et un serveur
  abandonné pèsent autant.

## Où sont les tokens, et ce qu'une version a changé

Un total cache quelle partie d'une liste a grossi. Un lecteur (Raju Dandigam) a demandé les parties séparément et
une vérification qui tourne à chaque version : HISTOR répartit désormais chaque compte entre les noms des outils,
les descriptions, les schémas d'entrée, et les clés et la ponctuation autour. Les quatre font le total ; les schémas
de sortie et la liste telle que servie sont donnés à côté. `@playwright/mcp` à la dernière observation de HISTOR :
3 762 tokens, dont schémas d'entrée 3 123 (83 %), descriptions 394, noms 120.

Lors du passage de la nuit du 22 au 23 octobre, 109 changements avaient une liste des deux côtés. 95 ont alourdi la
liste, dont 30 de plus d'un dixième ; avec une limite de 10 % sur le total, les descriptions et les schémas d'entrée
chacun, 40 auraient échoué à la vérification. Dans 56 des 95, les schémas d'entrée ont grossi plus que les
descriptions. Un paquet a ajouté une option `raw` à ses 15 outils existants : 708 tokens par requête, visibles dans
la répartition par propriété ci-dessous. Un outil renommé compte comme un retiré et un ajouté.

**Chaque changement que HISTOR enregistre porte son coût.** `/api/v1/changes/<id>/cost` donne chaque total avant et
après, les outils ajoutés et retirés, et chaque outil dont le compte a bougé, description, schéma d'entrée et chaque
propriété de premier niveau de ce schéma à part.
`/api/v1/servers/<id>/cost` donne la liste actuelle par outil et les totaux de ses derniers changements. Pour un
paquet, un changement est une version que HISTOR a exécutée et qui liste d'autres outils : c'est donc un historique
par version de chaque paquet du journal, sans rien installer ([api.fr.md](api.fr.md)).

**Avant la publication, dans votre propre CI,** `histor tool-cost` compte votre serveur de la même façon et fait
échouer le job quand le total, les descriptions ou les schémas d'entrée dépassent votre limite :

```bash
pip install "aimarket-histor[cost]"
# une fois, puis chaque fois que vous acceptez une hausse : committez l'instantané
histor tool-cost snapshot --stdio "node dist/index.js" -o tool-cost.json
# en CI : comparer, écrire le résumé du job, sortir avec 1 en cas de régression
histor tool-cost check --baseline tool-cost.json --stdio "node dist/index.js" --max-increase 10%
```

Les outils viennent de `--stdio COMMAND` (lancé, interrogé par `initialize` et `tools/list`, arrêté), de
`--url URL` (streamable HTTP, adresses locales permises) ou de `--tools FILE` (un tools/list enregistré). Chaque
total est vérifié à part : un schéma qui double fait échouer la vérification même quand des descriptions raccourcies
le cachent dans la somme. Une hausse de moins de 50 tokens n'échoue jamais (`--noise`), `--budget N` plafonne la
liste entière, `--update` réécrit la référence et `histor tool-cost diff A B` compare deux instantanés ou listes
enregistrées. Dans GitHub ou Gitea Actions :

```yaml
- run: pipx run --spec "aimarket-histor[cost]" histor tool-cost check --baseline tool-cost.json --stdio "node dist/index.js"
```

Le tableau arrive dans le résumé du job.

## Reproduire

[`scripts/tool_list_tokens.py`](../scripts/tool_list_tokens.py) lit un export des listes d’outils actuelles (la
requête est dans sa docstring) et écrit le résumé. Le nôtre, sans noms de serveurs :
[`tool-list-tokens-2026-10-19.json`](tool-list-tokens-2026-10-19.json).
