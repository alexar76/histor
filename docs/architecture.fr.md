# Architecture

> 🌐 [English](architecture.md) · [Русский](architecture.ru.md) · [Español](architecture.es.md) · **Français** · [中文](architecture.zh.md)

HISTOR est un service Python unique accompagné d’un processus auxiliaire. Tout ce qu’il sait provient
de deux lectures — le registre MCP officiel et le `tools/list` de chaque serveur répertorié — et tout
ce qu’il publie est signé par une seule clé et ajouté à un seul journal.

```mermaid
flowchart TB
    subgraph Outside["Extérieur, non fiable"]
        REG["registry.modelcontextprotocol.io<br/>/v0/servers?version=latest"]
        MCP["~20k points de terminaison MCP distants<br/>streamable-http"]
        CLIENT["clients, registres,<br/>auteurs de serveurs"]
    end
    subgraph Service["Service HISTOR (python -m histor serve)"]
        SCHED["planificateur<br/>toutes les HISTOR_CRAWL_INTERVAL_S"]
        CRAWL["robot d’exploration<br/>collecte · observation · enregistrement"]
        GUARD["netguard<br/>résout une fois, refuse le privé,<br/>épingle l’adresse contrôlée"]
        READER["client MCP en lecture seule<br/>initialize · tools/list"]
        SUBJ["sujet MTL/1 + empreintes"]
        LABELS["émetteur d’étiquettes<br/>AWR/2 · did:key"]
        LOG["logbook<br/>feuilles RFC 9162 + STH (tête d’arbre signée)"]
        API["FastAPI<br/>interface web · API · badge · flux · /check"]
    end
    SIDE["sidecar WARDEN<br/>node scanner/scan.mjs<br/>@aimarket/warden@0.5.0"]
    DB[("Postgres (prod)<br/>SQLite (dev)")]
    KEY[["/data/issuer.key<br/>/data/provider.key"]]

    SCHED --> CRAWL
    CRAWL --> REG
    CRAWL --> READER --> GUARD --> MCP
    CRAWL --> SUBJ --> LABELS --> LOG --> DB
    CRAWL -- "nouveaux ensembles d’outils, par lots" --> SIDE
    LABELS --- KEY
    API --> DB
    CLIENT --> API
```

## Modules

| Module | Responsabilité |
|---|---|
| `histor/registry.py` | Parcourt les pages du registre officiel (`version=latest`, avec nouvelles tentatives : une page prend de 1 à 50 s), garde un enregistrement par nom et fait de chaque entrée distante une cible. Les points de terminaison (endpoints) qu’il ne contactera pas sont conservés avec une raison (`transport-not-observed`, `templated-url`, `cleartext-url`), pour que les chiffres publics ne réduisent jamais leur dénominateur. |
| `histor/netguard.py` | Résout l’hôte une seule fois, refuse la cible si **une seule** des réponses n’est pas publique, puis se connecte à l’adresse contrôlée avec `Host` et le SNI TLS réglés sur le nom. Le DNS rebinding n’obtient jamais de seconde résolution. |
| `histor/mcpclient.py` | `initialize` → `notifications/initialized` → `tools/list` parcouru via `nextCursor`. Flux plafonné en taille, SSE lu jusqu’à notre identifiant de réponse, aucune redirection, membres JSON en double refusés. Il ne contient aucun chemin de code qui appelle un outil. |
| `histor/subject.py` | Le descripteur MTL/1 et les deux empreintes (digest), calculés avec le canonicaliseur de référence AWR/2. Un test de parité octet par octet le confronte au `mtl_subject.py` du profil lui-même. |
| `histor/scanner.py` + `scanner/scan.mjs` | Exécute les contrôles WARDEN **publiés**. Au démarrage, le sidecar décrit son propre ensemble de motifs et son ensemble d’enregistrements, si bien qu’une étiquette nomme l’empreinte des règles réellement exécutées. |
| `histor/labels.py` | Quatre méthodes d’étiquetage, chacune produisant un document AWR/2 signé. Les règles de verdict de MTL/1 sont appliquées dans le code (`MTL-METH-002`). |
| `histor/logbook.py` + `histor/merkle.py` | Ajout de feuilles, têtes d’arbre, preuves d’inclusion et de cohérence. Seuls les sous-arbres complets sont stockés, donc chaque preuve coûte O(log n) lectures. |
| `histor/crawler.py` | Une exploration (crawl) : collecte → observation (pool de threads, limite par hôte, hôtes entrelacés) → analyse des nouveaux ensembles d’outils → une transaction par cible, par lots de `HISTOR_CRAWL_BATCH` enregistrés avant de lire le suivant → signature d’une tête d’arbre. |
| `histor/check.py` | `/check` : compare l’ensemble d’outils d’un client avec ce qui a été observé ; décompte d’empreintes facultatif, sur option. |
| `histor/app.py` | La surface HTTP : interface web, API, badges, flux Atom, route opérateur et le peer de fédération AIMarket. |
| `histor/db.py` + `histor/migrations.py` + `histor/store.py` | Stockage indépendant du backend : un seul dialecte SQL pour SQLite et Postgres, des migrations numérotées, un contrat de colonnes contrôlé au démarrage. |

## Une exploration

```mermaid
sequenceDiagram
    autonumber
    participant S as planificateur
    participant C as robot d’exploration
    participant R as registre
    participant M as points de terminaison MCP
    participant W as sidecar WARDEN
    participant DB as stockage
    S->>C: intervalle écoulé (ou POST /api/v1/admin/crawl de l’opérateur)
    C->>R: GET /v0/servers?version=latest (paginé, avec nouvelles tentatives)
    R-->>C: ~21k serveurs, ~22k entrées distantes
    C->>DB: upsert des cibles, marquage des retraits (seulement après une collecte complète)
    C->>W: describe → ensemble de motifs + ensemble d’enregistrements, avec leurs empreintes
    loop lots de 400 points de terminaison, chacun enregistré avant de lire le suivant
    par 12 workers, ≤ 2 par hôte, hôtes entrelacés
        C->>M: initialize, tools/list (toutes les pages)
        M-->>C: ensemble d’outils ou un statut (http-401, timeout, …)
    end
    C->>W: analyse des ensembles d’outils jamais vus (lots de 64)
    loop une transaction par cible
        C->>DB: ligne d’observation
        C->>DB: nouveau sujet → étiquettes observation + analyse + nom, ajoutées
        C->>DB: étiquette antérieure → étiquette de continuité (pass / fail / inconclusive)
        C->>DB: fail → ligne de changement avec un diff par outil
    end
    end
    C->>DB: signature d’une tête d’arbre sur la nouvelle taille
```

## Quand une étiquette est émise

Le journal doit consigner des faits, pas du bruit : des résultats déterministes identiques ne sont
donc pas réémis chaque jour.

```mermaid
stateDiagram-v2
    [*] --> Unseen
    Unseen --> Pinned: tools/list ok<br/>étiquettes observation + analyse + nom
    Unseen --> Undigestible: MTL-SUBJ-001/002/003<br/>une observation inconclusive
    Pinned --> Pinned: même empreinte, ≥ 20 h depuis le dernier maillon<br/>continuité pass (unchangedSince)
    Pinned --> Changed: empreinte différente<br/>continuité fail + nouvelles étiquettes observation/analyse + diff
    Changed --> Pinned: exploration suivante, même empreinte<br/>continuité pass
    Pinned --> Unreachable: statut ≠ ok<br/>UNE SEULE continuité inconclusive
    Unreachable --> Unreachable: échec persistant<br/>rien n’est émis
    Unreachable --> Pinned: de retour avec la même empreinte<br/>continuité pass
    Unreachable --> Changed: de retour avec une nouvelle empreinte<br/>continuité fail
```

Un nouvel ensemble de motifs ou d’enregistrements WARDEN (un nouveau paquet publié) réémet les
étiquettes d’analyse de chaque sujet courant, car deux étiquettes d’analyse ne sont comparables que
sous le même ensemble.

## Modèle de données

```mermaid
erDiagram
    targets ||--o{ observations : "une par tentative"
    targets ||--o{ labels : "émises à son sujet"
    targets ||--o{ changes : "continuité fail"
    targets ||--o{ client_reports : "décomptes d’empreintes sur option"
    labels ||--|| log_nodes : "feuille = niveau 0"
    blobs ||--o{ labels : "éléments de preuve par empreinte"
    sths }o--|| log_nodes : "s’engage sur la racine"
    targets {
        text id PK "sha256(name, endpoint)[:16]"
        text name
        text endpoint
        text current_subject "empreinte du sujet (SRI)"
        text current_toolset "empreinte de l’ensemble d’outils (SRI)"
        text unchanged_since
        text chain_label "dernier maillon pour la continuité"
    }
    labels {
        text id PK "urn:uuid"
        text method
        text verdict
        text digest UK "SRI de JCS(étiquette)"
        bigint leaf_index UK
        text body "le document signé"
    }
    log_nodes {
        int level PK
        bigint idx PK
        text hash "sous-arbres complets uniquement"
    }
    blobs {
        text digest PK
        text kind "toolset · descriptor · pattern-set · record-set"
        text body
    }
```

## Stockage et migrations

SQLite est le stockage de développement : un fichier unique sous `HISTOR_DATA_DIR`, en mode WAL, une
connexion par lecture, pour qu’aucun lecteur ne reste bloqué sur un ancien instantané et n’empêche les
checkpoints. Postgres est le stockage de production : `HISTOR_PROFILE=prod` refuse de démarrer sans
URL `postgresql://`. Tout le SQL est écrit une seule fois, dans le sous-ensemble commun aux deux
moteurs ; là où ils divergent (colonnes d’identité), une migration porte une instruction par backend.
Les ajouts au journal prennent un verrou consultatif (advisory lock) Postgres à l’intérieur de leur
transaction : deux processus pointés vers la même base ajoutent donc quand même les feuilles une par
une.

Les migrations suivent le style maison partagé avec HESTIA et le Hub : une table `schema_migrations`,
une liste de révisions numérotées, chaque révision dans sa propre transaction, échec bruyant
(fail-loud). Une révision livrée n’est jamais modifiée ; une base qui a appliqué une révision que ce
build ne connaît pas est refusée.

## Clés

| Fichier | Signe | Pourquoi séparé |
|---|---|---|
| `issuer.key` | chaque étiquette (AWR/2, `did:key`), chaque tête d’arbre (`histor.sth/v1`), chaque réponse `/check` (`histor.check/v1`) | une seule identité pour tout ce qu’un lecteur vérifie ; le `type` inclus dans les octets signés empêche qu’une signature portant sur un type de document soit rejouée comme un autre |
| `provider.key` (+ `_mldsa`) | les documents de fédération AIMarket (`.well-known`, manifeste, reçus d’interopérabilité), en hybride Ed25519 + ML-DSA-65 quand `HISTOR_PQC=1` | parle le protocole du Hub ; aucune des deux clés ne peut signer pour l’autre |

Les deux résident sur le volume de données, en mode 0600, jamais dans l’environnement ni dans la base
de données ; les liens et les fichiers corrompus sont refusés au démarrage.
