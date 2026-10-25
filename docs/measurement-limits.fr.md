# Lire les mesures HISTOR

> 🌐 [English](measurement-limits.md) · [Русский](measurement-limits.ru.md) · [Español](measurement-limits.es.md) · **Français** · [中文](measurement-limits.zh.md)

Précisions issues des questions des lecteurs, 2026-10-25. Ce sont les limites d’observations et de comparaisons, pas des garanties sur chaque installation ou serveur actif. Source locale, service déployé et distribution Python publiée sont des états distincts.

## Un échec d’installation n’est pas un diagnostic

`sdk-incompatible` est une heuristique sur le texte de sortie. L’ancien classificateur incluait extras manquants et erreurs MCP 1/Pydantic lorsque le traceback mentionnait FastMCP ; il ne prouvait pas qu’une dépendance non bornée avait installé MCP 2. Un essai diagnostique `mcp<2` change l’environnement et ne remplace ni l’observation principale d’installation neuve ni son ensemble de tools.

La cohorte reconstruite de 292 paquets a été retestée sous Linux/gVisor sans notre `mcp<2` imposé : 12 nouvelles versions ont listé leurs tools, deux versions inchangées ont fonctionné avec leur extra MCP déclaré, 276 échouaient encore et deux exigeaient une vraie authentification ou un index de projet préparé. Onze récupérations ont un contrôle fraîchement installé de l’ancienne version qui échoue ; une ancienne version était indisponible. Cela ne démontre ni 292 échecs MCP 2, ni sécurité, ni fonctionnement authentifié complet. [Résultats et contrôles](https://dev.to/alexar76/we-rechecked-292-failing-mcp-packages-12-newer-releases-now-list-tools-p72).

## Tokens et CI

Vérifier séparément total, descriptions et schémas d’entrée : une réduction peut masquer une autre hausse. Garder une baseline relue, sans la recréer juste avant chaque check. L’[exemple public copiable](https://dev.to/alexar76/a-copyable-mcp-tool-cost-check-for-ci-with-passing-and-failing-examples-3hl5) épingle CLI publié 0.3.1 et tokenizer 0.14.0, vérifie des snapshots enregistrés et n’appelle aucun tool textokit. Le vrai changement est 5 565 → 3 776 tokens ; inverser la comparaison retourne volontairement **1**, sans établir une régression ultérieure. Le sens normal retourne **0**, une entrée invalide **2**.

Le comptage porte sur une représentation compacte définie avec `o200k_base`. Wrappers, sous-ensembles de tools, chargement à la demande, tokenizer et écritures/lectures du cache modifient l’usage réel. Les tableaux historiques sont des scénarios datés, ni factures ni prix actuels ; il faut les relevés d’usage du client pour une facture. [Guide des coûts](tool-list-cost.fr.md).

## Numérique ne signifie pas cosmétique

Les vues masquant nombres ou espaces aident la revue ; elles ne remplacent ni le digest MTL canonique littéral ni continuity. $0.002 → $0.02 est numérique mais potentiellement important : [changement 13185](https://histor.modelmarket.dev/api/v1/changes/13185). Distinguer serveurs et transitions, descriptions et schémas/composition, indicateurs superposés et catégories exclusives, états raw manquants et états inchangés. [Étiquettes et digests](labels.fr.md).

## Un paiement n’identifie pas un client

`histor payer-audit`, disponible dans la source, sépare règlement, signaux comportementaux et annotations attribuées à des transactions. La vérification PyPI du 2026-10-25 retournait **0.3.1** ; cette distribution n’incluait pas la commande. Une version écrite dans un checkout ne prouve pas sa publication. [Exécution depuis la source, EN](payer-audit.md).

Une correspondance de transfert Base USDC ne prouve ni livraison, ni propriétaire du portefeuille, ni intention. Le marqueur own-wallet du vendeur et une affirmation de correspondance inputExample restent des déclarations attribuées sans contrôle indépendant. `unknown` ne confirme pas un client organique ; `probe-like` est un indice non calibré, ni fraude ni probabilité. Un paiement de recherche ne classe pas définitivement le portefeuille. Tous les paiements, probes et surveillance compris, restent dans les totaux monétaires. [Exemple des cinq payeurs](https://dev.to/alexar76/five-wallets-paid-an-x402-seller-what-can-we-say-about-demand-5gek).

## Le sondage manque des états intermédiaires

Remote HTTP a sa propre file : six heures normalement, objectif de 30 minutes après au moins deux changements observés sur sept jours. Découverte du registre et planification des paquets gardent leur calendrier quotidien. Ce sont des cibles, pas des délais garantis : panne, retard, rate limits et backoff agrandissent les écarts. Consulter `remotePolling` dans `/api/v1/stats` et les écarts réels. A→B→A entièrement entre deux lectures reste invisible ; augmenter la fréquence réduit ce manque sans enregistrer chaque transition. [Détails opérationnels, EN](remote-polling.md).

## Limites du canary et du launcher

Un `initialize`/`tools/list` réussi ou un canary synthétique ne teste pas toute entrée et ne prouve pas la gratuité. Le canary peut choisir un preview gratuit tandis que l’absence d’arguments active un défaut payant. `readOnlyHint` reste une déclaration du serveur, pas une attestation de facturation ; erreur ou timeout ne prouve pas l’absence d’effet. Lectures distantes et canaries de paquets sont des mesures différentes.

Pour comparer des launchers, conserver deux snapshots complets, commande exacte, versions runtime/runner et conditions. Version du paquet, runner exécuté et `serverInfo.version` autodéclaré peuvent différer. Lire des observations historiques n’est ni une nouvelle exécution ni une étude d’utilisabilité humaine ; annotations et versions exigent leur propre provenance.
