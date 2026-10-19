# Ce que coûte une liste d’outils

> 🌐 [English](tool-list-cost.md) · [Русский](tool-list-cost.ru.md) · [Español](tool-list-cost.es.md) · **Français** · [中文](tool-list-cost.zh.md)

Chaque serveur MCP auquel un client se connecte énumère ses outils : pour chacun, un nom, une description et un
schéma d’entrée. Le client met cette liste dans le prompt de chaque requête au modèle, quoi que demande
l’utilisateur. Un lecteur de notre étude de 12 915 paquets (Paul Irolla) a proposé de le compter en tokens. HISTOR
conserve la liste d’outils actuelle de chaque serveur qu’il observe : nous avons compté.

## Les chiffres

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

| La liste d’un serveur | Tokens | 0,10 $ le million (Claude Haiku 5.5) | 2 $ le million (Claude Sonnet 5.5) | Sonnet 5.5, lue en cache (0,10 $ le million) |
|---|---|---|---|---|
| paquet médian | 1 272 | 0,13 $ | 2,54 $ | 0,13 $ |
| paquet moyen | 3 812 | 0,38 $ | 7,62 $ | 0,38 $ |
| 90e centile | 8 361 | 0,84 $ | 16,72 $ | 0,84 $ |
| 99e centile | 39 612 | 3,96 $ | 79,22 $ | 3,96 $ |

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

## Reproduire

[`scripts/tool_list_tokens.py`](../scripts/tool_list_tokens.py) lit un export des listes d’outils actuelles (la
requête est dans sa docstring) et écrit le résumé. Le nôtre, sans noms de serveurs :
[`tool-list-tokens-2026-10-19.json`](tool-list-tokens-2026-10-19.json).
