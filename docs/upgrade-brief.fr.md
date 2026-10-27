# Guide HISTOR pour analyser les mises à jour MCP

[English](upgrade-brief.md) · [Русский](upgrade-brief.ru.md) · [Español](upgrade-brief.es.md) · [Français](upgrade-brief.fr.md) · [中文](upgrade-brief.zh.md)

Le prototype local `histor upgrade-brief` relie les changements des définitions d’outils MCP à
vos flux de travail. Fournissez deux observations enregistrées par dépendance et une sélection
explicite d’outils. Il produit `report.md` avec des vérifications à refaire et `report.json` avec
les différences, les versions, les limites de comparaison et les empreintes SHA-256 des entrées.

Utilisez la copie locale actuelle du code source pour ce prototype. Le rapport est généré de façon
déterministe, hors ligne, sans LLM, base de données, identifiants ni configuration de production.
Les descriptions d’outils sont traitées comme des données. Une définition modifiée justifie de
retester un flux ; elle ne démontre pas à elle seule un changement de comportement ou une mise
à jour défectueuse.

## Exécuter et vérifier l’exemple

Python 3.11 ou une version ultérieure est nécessaire. Depuis le répertoire `histor/` de cet espace
de travail, exécutez :

```bash
python3 --version
python3 -m histor upgrade-brief \
  --lang fr \
  --watchlist examples/upgrade-brief/watchlist.fr.json \
  --out-dir ../output/histor-upgrade-brief-example/fr
python3 examples/upgrade-brief/verify.py \
  --out-dir ../output/histor-upgrade-brief-example
```

Si votre interpréteur compatible est `.venv/bin/python`, remplacez `python3` par celui-ci dans chaque
commande. L’exemple hors ligne ne nécessite aucune installation de paquets. Le vérificateur exécute
deux fois les cinq listes de suivi, contrôle les changements et les états des flux attendus,
vérifie que les résultats sont identiques octet par octet et confirme que les entrées sont restées
inchangées. En cas de succès, il affiche `PASS` et se termine avec `0` ; un contrôle échoué donne
`1`. Chaque répertoire de langue contient `report.md` et `report.json`. Relancer les commandes
remplace ces rapports générés.

Le vérificateur exécute aussi deux fois la commande sans `--lang` et exige une sortie identique à celle de `--lang en` : 12 exécutions hors ligne au total.

Les six observations sont synthétiques. Aucun serveur MCP n’est lancé et aucun outil n’est appelé.
Le [parcours de l’exemple](../examples/upgrade-brief/README.fr.md) explique les résultats attendus
et les vérifications concrètes. Les instructions, les titres des listes et les descriptions des flux sont disponibles en cinq langues.

## Choisir la langue du rapport

`--lang` accepte `ru`, `en`, `es`, `fr` ou `zh`. La valeur par défaut est l’anglais (`en`) dans la CLI, l’API Python et le rendu lorsque le champ `language` est absent. Ce paramètre choisit la langue des titres et explications
intégrés au Markdown, des vérifications des outils et des flux, ainsi que des textes de vérification
et des limites en JSON. Le champ JSON `language` enregistre ce choix. Les noms de champs, codes
d’état, motifs de diagnostic, ID, schémas et empreintes restent inchangés.

Les titres des listes, descriptions des flux et extraits des définitions sont conservés tels
qu’ils sont fournis. Choisissez la liste localisée de l’exemple pour obtenir les libellés voulus ;
`--lang` ne traduit pas automatiquement les données d’entrée.

## Choisir les dépendances et les flux

Créez un répertoire contenant `watchlist.json` et `snapshots/`. Les chemins des instantanés sont
relatifs au répertoire de la liste et doivent rester à l’intérieur. Les URL, chemins absolus,
composantes `..` et liens symboliques pointant en dehors du répertoire sont refusés.

```json
{
  "format": "histor.upgrade-watchlist/v1",
  "title": "Notre flux de recherche",
  "dependencies": [{
    "id": "our-research-server",
    "before": "snapshots/before.json",
    "after": "snapshots/after.json",
    "tools": ["search_docs"],
    "workflows": [{
      "id": "weekly-review",
      "description": "Préparer la revue hebdomadaire des documents",
      "tools": ["search_docs"]
    }]
  }]
}
```

Chaque dépendance doit avoir une sélection non vide d’outils et au moins un flux. Un flux ne peut
utiliser que des outils sélectionnés pour cette dépendance. Conservez les ID de dépendance,
noms d’outils et ID de flux lors de la traduction des libellés. `before` ou `after` peuvent valoir
`null` ; une observation manquante laisse la comparaison inconnue. Pour la comparaison, le
`targetId` de chaque instantané doit correspondre à l’`id` de sa dépendance.

Exécutez votre ensemble de fichiers depuis `histor/` :

```bash
python3 -m histor upgrade-brief \
  --lang fr \
  --watchlist /path/to/bundle/watchlist.json \
  --out-dir /path/to/report
```

## Enregistrer une observation HTTP

La commande distincte `capture` envoie des requêtes réseau au point de terminaison indiqué. Elle
nécessite les dépendances d’exécution de HISTOR définies dans `pyproject.toml`. Si elles ne sont
pas installées, préparez d’abord un environnement depuis cette copie du code ; l’installation
peut accéder aux index de paquets :

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Capturez un point de terminaison HTTP MCP public avec son URL réelle et un nouveau nom de fichier :

```bash
.venv/bin/python -m histor upgrade-brief capture \
  --url https://your-public-server.example/mcp \
  --target-id our-research-server \
  --out /path/to/bundle/snapshots/before.json
```

L’URL `.example` et les chemins `/path/to/` sont des valeurs à remplacer. Répétez après la mise
à jour avec `after.json`, puis générez le rapport hors ligne. La capture utilise discovery,
l’initialisation legacy si nécessaire et toutes les pages de `tools/list`. Elle n’appelle jamais
d’outils, n’installe et ne lance aucun paquet MCP. Elle ne prend pas en charge l’authentification
du point de terminaison et refuse les URL contenant des informations utilisateur, une requête
ou un fragment. Les adresses privées sont bloquées par défaut ; utilisez explicitement
`--allow-private` pour votre propre point de terminaison de test. `--timeout` vaut 20 secondes
par défaut et accepte une valeur strictement positive, inférieure ou égale à 300.

La capture ne remplace jamais un instantané existant. Le code de sortie `0` signifie qu’une
observation complète a été enregistrée ; `1`, qu’une observation échouée a été enregistrée ;
`2`, une entrée invalide ou une erreur de sortie. Une observation échouée ne représente pas une
liste vide obtenue avec succès. L’instantané capturé est une preuve locale non signée : les
empreintes du rapport détectent les modifications des entrées sans authentifier leur créateur.

## Préserver la provenance des observations

```json
{
  "format": "histor.upgrade-observation/v1",
  "targetId": "our-research-server",
  "status": "ok",
  "observedAt": "2026-10-27T09:00:00Z",
  "packageVersion": null,
  "serverName": "our-research-server",
  "serverVersion": "1.2.0",
  "protocol": "2026-07-28",
  "extrasCoverage": "observed",
  "launch": {"transport": "streamable-http", "endpoint": "https://your-public-server.example/mcp"},
  "tools": [{"name": "search_docs", "description": "Search documents", "inputSchema": {"type": "object"}}]
}
```

Une observation réussie doit contenir le tableau `tools` complet. Conservez l’horodatage réel avec
fuseau horaire, le protocole observé et le contexte de lancement. Distinguez la version observée
du paquet de la `serverVersion` déclarée par le serveur lui-même. Pour un point de terminaison
distant, `packageVersion` peut valoir `null`. Le rapport expose une empreinte de `launch` et le
transport connu, sans recopier commandes, arguments ou valeurs d’environnement ; les instantanés
d’origine peuvent toujours contenir des données sensibles.

Lors de l’import de données HISTOR, associez les définitions à l’observation historique exacte.
L’implémentation locale de l’API expose `timeline[].protocolVersion` ; copiez ce champ dans
`protocol` lorsqu’il est présent. Une API plus ancienne ou déployée peut l’omettre : conservez
cette lacune. Joignez les `toolExtras` historiques aux outils par nom uniquement lorsqu’ils
appartiennent à cette observation. Les définitions et extras actuels ne peuvent pas compléter
un ancien instantané. Le prototype ne propose pas d’importateur automatique de l’API.

Pour une source MTL/1 ne contenant que `name`, `description`, `inputSchema` et `outputSchema`,
définissez `extrasCoverage` à `"unknown"`. Le rapport compare ces champs de base, ignore la
comparaison des extras et laisse la comparabilité globale inconnue. `"observed"` signifie que
l’instantané contient tous les extras annoncés dans cette observation ; c’est aussi la valeur
par défaut des entrées locales v1 existantes.

## Lire les résultats et choisir les vérifications

| État de la dépendance | Signification et action suivante |
|---|---|
| `changed` | Des définitions observées comparables ont changé. Vérifiez les outils sélectionnés affectés et leurs flux. |
| `unchanged` | Les définitions observées comparables n’ont pas changé. Le comportement à l’exécution n’a pas été testé. |
| `context_changed` | Des conditions connues du protocole ou du lancement ont changé. Recréez des conditions comparables avant d’attribuer les différences à une mise à jour du paquet. |
| `unknown` | Il manque une paire d’observations réussies ou le contexte de comparaison, les dates sont désordonnées, un outil sélectionné est absent des deux côtés ou la couverture des extras est inconnue. Résolvez les lacunes indiquées. |

Les états des flux sont `retest`, `no_observed_change` et `unknown`. Une dépendance peut changer
en dehors des outils sélectionnés ; utilisez l’état de chaque flux pour définir les vérifications
à refaire. `fieldCoverage` vaut `full`, `core-only` ou `none`. Les différences connues restent
visibles même lorsque la comparabilité globale est inconnue. Les indications des annotations
restent des déclarations du serveur.

`serializedUtf8Bytes` mesure le JSON compact présenté au modèle en octets UTF-8. Il ne mesure ni
tokens, ni facture, ni économies réalisées. Les valeurs SHA-256 identifient les octets exacts
des entrées. Des définitions inchangées ou un rapport produit avec succès ne démontrent ni la
correction ni la sécurité du comportement à l’exécution.

## Limites et dépannage

Le prototype accepte jusqu’à 30 dépendances, 128 outils sélectionnés par dépendance, 64 flux par
dépendance et 2000 outils par instantané. Une liste de suivi est limitée à 256 000 octets et chaque
instantané à 2 000 000 octets. Les clés JSON dupliquées, champs de format inconnus, nombres non
finis, définitions d’outils invalides et structures dépassant 48 niveaux ou 100 000 nœuds sont refusés.

La commande de rapport se termine avec `0` lorsque les rapports sont écrits, même avec des résultats
`unknown` ; `2` indique une entrée invalide ou une erreur d’écriture. Un répertoire de sortie existant
est réutilisable : `report.md` et `report.json` sont remplacés, mais l’écrasement d’un fichier
d’entrée est refusé. Pour `No module named histor`, exécutez depuis le répertoire `histor/` de la
copie du code. Pour `unknown`, consultez `limitations`, `sources` et `fieldCoverage` avant de
prendre une décision sur la mise à jour.
