# Reproduire une analyse de mises à jour MCP avec HISTOR

[English](README.md) · [Русский](README.ru.md) · [Español](README.es.md) · [Français](README.fr.md) · [中文](README.zh.md)

Cet exemple compare six observations synthétiques enregistrées pour trois dépendances. Il relie
les changements de définition à quatre flux de travail et propose des vérifications concrètes
avant une mise à jour. Les données de test ne décrivent aucun paquet publié, point de terminaison
actif ni déploiement client.

## Exécuter depuis le code source

Utilisez Python 3.11 ou une version ultérieure, depuis `histor/`. Aucune installation, aucun
identifiant, modèle ou accès réseau n’est nécessaire :

```bash
python3 -m histor upgrade-brief \
  --lang fr \
  --watchlist examples/upgrade-brief/watchlist.fr.json \
  --out-dir ../output/histor-upgrade-brief-example/fr
python3 examples/upgrade-brief/verify.py \
  --out-dir ../output/histor-upgrade-brief-example
```

Utilisez `.venv/bin/python` à la place de `python3` si c’est votre interpréteur Python 3.11+.
La première commande écrit `report.md` et `report.json`. La seconde vérifie deux fois les listes
des cinq langues par rapport à [expected.json](expected.json), contrôle que les sorties répétées
sont identiques et les entrées inchangées, puis affiche `PASS` et se termine avec `0`. Un contrôle
échoué donne `1`. Les rapports sont enregistrés dans `en/`, `ru/`, `es/`, `fr/` et `zh/` ; une
nouvelle exécution remplace les rapports générés.

Le vérificateur exécute aussi deux fois la commande sans `--lang` et exige une sortie identique à celle de `--lang en` : 12 exécutions hors ligne au total. Le `watchlist.json` principal est en anglais ; `watchlist.en.json` reste un alias équivalent.

## Résultats attendus

Le résumé est `changed: 1`, `unchanged: 0`, `context_changed: 1`, `unknown: 1`.

| Dépendance | Résultat | Résultat des flux |
|---|---|---|
| `demo:research` | `changed`, définitions comparables | `weekly-review` et `archive-import` : `retest` |
| `demo:migration` | `context_changed`, protocole modifié | `status-check` : `unknown` |
| `demo:offline` | `unknown`, nouvelle observation échouée | `daily-status` : `unknown` |

Pour `weekly-review`, rejouez une requête `search_docs` enregistrée avec le nouveau champ
obligatoire `workspace`. Vérifiez que `limit` est désormais déclaré comme chaîne et adaptez
la lecture du résultat de `items` à `results`. Lisez la description modifiée et confirmez
la sélection d’outils attendue. `export_brief` fait passer `readOnlyHint` de `true` à `false`
et `destructiveHint` de `false` à `true` ; examinez les confirmations et permissions avant
de tester une exportation. Ces indications sont des déclarations du serveur.

Pour `archive-import`, l’outil sélectionné `legacy_fetch` est supprimé. Le nouvel outil non
sélectionné `fetch_document` prend `id` au lieu de `document_id`. Examinez ce candidat, modifiez
explicitement la sélection du flux si cela convient et testez un document connu ; l’analyse
ne le désigne pas comme remplacement automatique.

Pour `status-check`, les définitions de `read_status` restent inchangées, mais le protocole passe
de `2025-06-18` à `2026-07-28`. Répétez les observations avec un client prenant en charge le
protocole voulu, dans les mêmes conditions de lancement et d’accès. Pour `daily-status`, obtenez
une nouvelle observation complète et réussie avant de comparer les outils. Une capture échouée
ne démontre pas la suppression d’outils.

Le vérificateur contrôle les définitions et la structure attendue du rapport. Les vérifications
de ces flux sont des instructions pour votre propre environnement de test ; l’exemple n’exécute
aucun serveur ou outil MCP.

## Variantes linguistiques et données personnelles

| Langue | Liste de suivi |
|---|---|
| English | [watchlist.json](watchlist.json) |
| Русский | [watchlist.ru.json](watchlist.ru.json) |
| Español | [watchlist.es.json](watchlist.es.json) |
| Français | [watchlist.fr.json](watchlist.fr.json) |
| 中文 | [watchlist.zh.json](watchlist.zh.json) |

Les variantes traduisent les titres et descriptions des flux tout en partageant les instantanés,
ID et outils sélectionnés. `--lang ru|en|es|fr|zh` choisit les explications et vérifications intégrées
au rapport, ainsi que les textes de vérification et limites en JSON. La valeur par défaut est
l’anglais (`en`). Les noms de champs, codes d’état, motifs de diagnostic, ID, schémas et empreintes sont
stables ; le champ JSON `language` enregistre le choix. Vos titres, descriptions de flux et
extraits des définitions sont conservés : associez l’option à la liste localisée correspondante ;
les données d’entrée ne sont pas traduites automatiquement.

Pour utiliser vos données, copiez l’ensemble des fichiers dans un autre répertoire, remplacez les
instantanés et modifiez les sélections d’outils et les flux. `expected.json` et `verify.py` décrivent
cet exemple synthétique fixe ; de nouvelles données nécessiteront d’autres résultats attendus.
La commande distincte `capture` peut enregistrer des observations HTTP avec un accès réseau ;
consultez le [guide](../../docs/upgrade-brief.fr.md).
