# Exploitation

> 🌐 [English](operations.md) · [Русский](operations.ru.md) · [Español](operations.es.md) · **Français** · [中文](operations.zh.md)

## Topologie

```mermaid
flowchart LR
    NET(("Internet")) --> NGINX["nginx :443<br/>histor.modelmarket.dev<br/>TLS · en-têtes de sécurité"]
    NGINX --> APP["conteneur histor<br/>127.0.0.1:9490<br/>rootfs en lecture seule · cap-drop ALL"]
    APP --> PG[("histor-postgres<br/>réseau compose uniquement")]
    APP --> VOL[["volume histor-data<br/>issuer.key · provider.key"]]
    APP -- "initialize + tools/list<br/>≤ 12 workers, ≤ 2 par hôte" --> MCP["points de terminaison MCP publics"]
    MON["Alien Monitor · SKOPOS"] -- "GET /health, /api/v1/stats" --> NGINX
```

## Déploiement

Depuis la racine du monorepo, sur un portable :

```bash
./scripts/deploy_histor.sh --remote admin-vps   # synchronise histor/ vers /opt/histor, puis déploie là-bas
```

`.env` n’est jamais copié depuis un portable : l’hôte garde le sien. La première exécution le crée (jeton d’opérateur
et mot de passe Postgres aléatoires, mode 0600, seuls les noms sont affichés) ; les suivantes ne le réécrivent jamais.
Le script étiquette l’image en service `histor-histor:prev`, construit et démarre la nouvelle, et remet la précédente
si `/health` ne répond pas. Il installe le vhost nginx depuis `deploy/nginx/histor.modelmarket.dev.conf` (dans le
satellite), émet le certificat avec certbot s’il n’existe pas, installe le minuteur de sauvegarde nocturne (voir
« Sauvegardes ») et, si une exploration tournait au démarrage, la relance ; avec les lots, elle perd au plus un lot.

## Un redémarrage en plein parcours

Un redémarrage (un déploiement, un plantage, un arrêt faute de mémoire) ne coûte pas le parcours. Le
résultat de chaque cible est enregistré dès qu'il arrive, et le démarrage suivant poursuit le cycle interrompu :

- **Ce qui est lu reste lu.** La nouvelle exécution n'interroge que ce que le cycle n'a pas encore tenté. Le
  cycle est poursuivi depuis aussi longtemps qu'il tourne, tant qu'il avançait : seul un processus arrêté
  pendant tout un `HISTOR_CRAWL_INTERVAL_S` commence un nouveau cycle. (Jusqu'au 2026-10-23, un cycle était
  abandonné dès que son début avait un jour : le 22 octobre, un cycle de 42 heures et six redémarrages a été
  repris depuis le premier serveur.)
- **Les paquets gardent leur plan et leur budget.** Un cycle planifie ses paquets une fois (demander à npm et
  PyPI la version de chaque paquet connu prenait 59 minutes) et garde le plan dans `crawl_plans` ; une
  exécution qui poursuit prend ce qui reste, et `HISTOR_SANDBOX_MAX_PER_CRAWL` comme le budget du
  classificateur sont ceux du cycle, pas de chaque exécution.
- **Les chiffres restent.** Une exécution enregistre ses comptes toutes les 30 secondes : une exécution
  interrompue dit ce qu'elle a fait. `/api/v1/stats` rend compte du cycle : `lastRun` donne ce que toutes ses
  exécutions ont enregistré (avec `cycle.startedAt` et `cycle.runs`), et `crawl.progress` compte le cycle,
  avec `thisRun` à côté.

Un redémarrage ne perd que le travail en cours : le lot de points de terminaison en lecture et les paquets qui
tournaient à ce moment dans un bac à sable. Ils sont relus.

`tests/integration/test_restart.py` le vérifie sur un vrai processus : il tue HISTOR par SIGKILL au
milieu d'un cycle (points de terminaison et paquets enregistrés, d'autres en cours), le relance et ne trouve
rien d'enregistré lu deux fois, aucune demande de version, le budget de paquets du cycle respecté et le cycle
rapporté en entier ; sous SQLite et PostgreSQL. Sur le code d'avant ce changement, il échoue.

## Configuration

| Variable | Défaut | Remarques |
|---|---|---|
| `HISTOR_PROFILE` | `dev` | `prod` refuse de démarrer sans les trois suivantes (fail-closed, refus par défaut) |
| `HISTOR_DATABASE_URL` | vide (SQLite) | prod : `postgresql://…` — obligatoire |
| `HISTOR_OPERATOR_TOKEN` | vide | prod : ≥ 24 caractères ; protège `/api/v1/admin/*` |
| `HISTOR_PUBLIC_BASE` | `http://127.0.0.1:9490` | prod : `https://…` ; utilisé dans les badges, le flux, la fédération |
| `HISTOR_CRAWL_INTERVAL_S` | `86400` | découverte du registre et paquets ; `0` = uniquement à la demande de l’opérateur |
| `HISTOR_REMOTE_INTERVAL_S` | `21600` | file HTTP distante indépendante, cible de six heures ; `0` remet les remotes au cycle quotidien |
| `HISTOR_REMOTE_CHANGED_INTERVAL_S` / `HISTOR_REMOTE_CHANGE_WINDOW_S` | `1800` / `604800` | cible de 30 minutes après deux changements observés sur sept jours ; [limites](measurement-limits.fr.md) |
| `HISTOR_CRAWL_MIN_GAP_S` | `1800` | le délai minimal après un cycle terminé avant le suivant ; l’instantané hebdomadaire (`deploy/export_dataset.sh`) est pris dans cet intervalle |
| `HISTOR_DECLARATION_REFRESH_S` | `3600` | fréquence de lecture, entre deux passages, des fichiers de déclaration des éditeurs ([api](api.fr.md)) ; `0` = seulement au passage |
| `HISTOR_CRAWL_ON_START` | `1` | `0` : une instance neuve attend un intervalle avant sa première exploration (crawl) |
| `HISTOR_CRAWL_WORKERS` / `_PER_HOST` / `_TIMEOUT_S` | `12` / `2` / `20` | politesse : un hôte qui porte 2 000 points de terminaison (endpoints) n’est jamais sollicité plus de deux fois à la fois |
| `HISTOR_CRAWL_LIMIT` | `0` | dev : n’observer que les N premiers points de terminaison |
| `HISTOR_CRAWL_BATCH` | `400` | points de terminaison observés, analysés et enregistrés ensemble ; un redémarrage perd au plus un lot, et le bureau affiche la progression à mesure que les lots sont enregistrés |
| `HISTOR_OPT_OUT` | vide | hôtes ou préfixes d’URL exclus à la demande, plus `opt-out.txt` sur le volume de données |
| `HISTOR_CHECK_RATE_PER_MIN` / `_SCAN_RATE_PER_HOUR` | `30` / `20` | par adresse client |
| `HISTOR_TRUSTED_PROXIES` | `127.0.0.1,::1` | le fichier compose ajoute `172.16.0.0/12` : le proxy de Docker se connecte depuis la passerelle du bridge |
| `HISTOR_PQC` | `0` | `1` : signatures de fédération hybrides Ed25519 + ML-DSA-65 |
| `HISTOR_RECEIPT_ISSUERS` | vide (fermé) | `did:key` séparés par des virgules dont le journal des reçus accepte les ancrages de reçus de travail (`/api/v1/receipts/*`). Le DID d'un hub se lit dans l'`issuer` de l'un de ses reçus réels. Un émetteur pas encore listé reçoit un refus réessayable : son hub garde l'ancrage en file jusqu'à ce que vous l'ajoutiez. `docker-compose.yml` doit la transmettre, comme chaque variable qu'il nomme |
| `HISTOR_ALLOW_PRIVATE_TARGETS` | `0` | tests uniquement ; refusé sous `prod` |
| `HISTOR_CLASSIFIER_MODEL` | vide | id du modèle OpenRouter (p. ex. `deepseek/deepseek-chat`, `minimax/minimax-m1`). Désactivé tant que modèle, clé et budget ne sont pas tous définis |
| `HISTOR_OPENROUTER_API_KEY` | vide | clé OpenRouter (ou `OPENROUTER_API_KEY`) ; reste dans le `.env` de l'hôte, jamais dans l'image |
| `HISTOR_CLASSIFIER_MAX_PER_CRAWL` | `0` | plafond d'appels payants par passage. `0` le désactive ; seuls les ensembles distincts comptent (un ensemble partagé ou déjà jugé est réutilisé sans frais) |
| `HISTOR_CLASSIFIER_BASE_URL` / `_TIMEOUT_S` / `_MAX_TOOLS` | `https://openrouter.ai/api/v1` / `30` / `60` | le classifieur sémantique, indépendant de la langue ; son verdict est indicatif et n'entre jamais dans le journal signé |

## Instantanés de données ouvertes

Une fois par semaine ISO, `histor-export-dataset` (`deploy/export_dataset.sh`, minuterie toutes les 10 minutes) copie un cycle d’exploration terminé dans `/var/lib/histor-datasets/<date>/`, le sert sur `/datasets/` et, si `/etc/histor-hf/` est configuré, le téléverse sur Hugging Face (étiquette `snapshot-<date>`). Les huit plus récents restent sur l’hôte (`HISTOR_DATASET_KEEP`) ; `HISTOR_DATASET_PERIOD=month` revient à un par mois. Le premier instantané de chaque mois est archivé à la main sur Zenodo, comme nouvelle version de l’enregistrement conceptuel : quand il est écrit, `histor-notify` envoie un message Telegram, et `histor-zenodo-reminder` (chaque jour à 09:00 UTC) le répète jusqu’à ce que Zenodo l’ait. Les deux lisent le jeton du bot et le chat dans `/etc/histor-notify.env` (0600 : `AICOM_ALERT_TELEGRAM_TOKEN`, `AICOM_ALERT_TELEGRAM_CHAT`) ; sans ce fichier, rien n’est envoyé. `histor-export-dataset --due` et `histor-zenodo-reminder --dry` disent ce qu’ils feraient.

Le sondage observe des échantillons, pas chaque transition : A→B→A entre deux lectures est invisible. La file distante indépendante ne réinstalle pas les paquets toutes les 30 minutes. Retards, limites par hôte et backoff allongent les écarts ; consulter `remotePolling` dans `/api/v1/stats`. Les [limites](measurement-limits.fr.md) distinguent cadence configurée et couverture mesurée.

## Migrations

```mermaid
flowchart LR
    START["histor serve"] --> BOOK["CREATE TABLE IF NOT EXISTS<br/>schema_migrations"]
    BOOK --> LOOP{"révision appliquée ?"}
    LOOP -- "non" --> TX["une transaction :<br/>instructions pour ce backend<br/>+ ligne de suivi"]
    TX --> LOOP
    LOOP -- "oui pour toutes" --> FUT{"révisions inconnues<br/>dans la base ?"}
    FUT -- "oui" --> STOP["refuser de démarrer"]
    FUT -- "non" --> CONTRACT{"colonnes de targets ==<br/>TARGET_COLUMNS ?"}
    CONTRACT -- "non" --> STOP
    CONTRACT -- "oui" --> SERVE["se mettre à l’écoute"]
```

```bash
docker compose exec histor python -m histor migrate status   # backend=postgresql applied=[1, 2, 3] pending=[]
docker compose exec histor python -m histor migrate up
```

Pour ajouter une révision, complétez la liste `MIGRATIONS` dans `histor/migrations.py` — ne modifiez
jamais une révision livrée — et mettez à jour `TARGET_COLUMNS` dans le même changement si elle touche
`targets`. La suite de tests applique la liste entière à SQLite, et à Postgres quand
`HISTOR_TEST_DATABASE_URL` est défini.

## Sauvegardes

Le journal est le produit, et la clé de l’émetteur est son identité : une nouvelle clé, c’est un nouveau journal, et
toute preuve de cohérence contre l’ancien `did:key` échoue par conception.

```mermaid
flowchart LR
    T["histor-backup.timer<br/>chaque nuit 03:40 UTC"] --> S["/usr/local/sbin/histor-backup"]
    S --> D["pg_dump -Fc<br/>vérifié par pg_restore --list"]
    S --> K["tar du volume histor-data<br/>issuer.key · clés du fournisseur · sth-marker.json"]
    D --> B[("/var/backups/histor<br/>0700 · 5 jours")]
    K --> B
    B -. "votre copie hors de l’hôte" .-> O[("une autre machine")]
```

`deploy_histor.sh` installe le minuteur ; lancez `sudo histor-backup` à la main quand vous voulez. Les copies locales
survivent à un volume perdu ou à une restauration ratée, pas à la perte de l’hôte : copiez aussi `/var/backups/histor`
ailleurs.

**Restauration** sur un nouvel hôte : déployez une fois (cela crée un journal vide et une clé), arrêtez le service,
restaurez les deux moitiés, puis redémarrez. La clé et la base doivent venir de la MÊME nuit :

```bash
docker compose -p histor stop histor
docker compose -p histor exec -T histor-postgres pg_restore -U histor -d histor --clean --if-exists < histor-<stamp>.dump
mkdir -p /tmp/histor-keys && tar -xf keys-<stamp>.tar -C /tmp/histor-keys && docker cp /tmp/histor-keys/data/. histor-histor-1:/data/
docker compose -p histor start histor
```

## La clé et le journal vont ensemble

À chaque démarrage, HISTOR vérifie que la clé du volume de données est celle qui a signé le journal de la base, et
que la base n’est pas plus ancienne que la tête d’arbre la plus récente signée par cette clé (`sth-marker.json`, à
côté de la clé). Il refuse de démarrer, en donnant la raison, quand :

- `issuer.key` manque alors que le journal contient déjà des étiquettes : une nouvelle clé commencerait un second journal sur l’ancien arbre ;
- la clé n’est pas celle du journal (`meta.issuer_did`) ;
- la tête la plus récente de la base est plus petite que celle du marqueur, ou différente : une restauration périmée
  ou une remise à zéro, avec laquelle la même clé signerait une seconde histoire.

Corrigez la cause (restaurez la clé ou la base correspondante de la même nuit). Ne commencez un nouveau journal que
volontairement : mettez de côté la base ET la clé avec le marqueur, ensemble.

## Supervision

- `/health` — `crawl_running`, `last_crawl_error` (lu dans la table runs : une exploration échouée ou interrompue
  survit à un redémarrage), `tree_size`.
- `/api/v1/stats` → `crawl.progress` pendant une exploration ; `lastRun.error` après un échec.
- `/api/v1/badges/sth` passe à l’ambre quand la tête la plus récente a plus de deux intervalles d’exploration : le robot s’est arrêté.
- Alien Monitor dessine HISTOR comme un nœud du groupe sécurité, alimenté par `/api/v1/stats` ; une dernière
  exploration échouée ou interrompue le fait passer au rouge.
- L’exploration quotidienne prend environ 1 à 2 heures avec la courtoisie par défaut (≈20 000 points de terminaison,
  quelques hôtes en portant des milliers chacun).

## Être un robot d’exploration courtois

HISTOR s’identifie (`User-Agent: histor/<version> (+https://histor.modelmarket.dev/#crawler; read-only: initialize + tools/list)`),
envoie trois messages JSON-RPC par point de terminaison et par jour, n’appelle jamais d’outil, ne suit aucune
redirection et garde au plus deux connexions par hôte. Une observation a une seule échéance pour toutes les requêtes et
pages, et l’ensemble d’outils est plafonné à 3 Mio et 5 000 outils.

Un exploitant qui veut que son point de terminaison soit exclu le demande dans une issue. Ajoutez l’hôte (qui couvre
ses sous-domaines) ou un préfixe d’URL à `HISTOR_OPT_OUT` (séparés par des virgules) ou à `opt-out.txt` sur le volume
de données (un par ligne, commentaires avec `#`), relu à chaque exploration. Le point de terminaison reste listé comme
non tenté avec la raison `operator-opt-out`, au lieu de disparaître en silence ; les étiquettes déjà dans le journal
restent, car le journal ne fait que croître.

## Bac à sable des paquets

La plupart des serveurs MCP qu’on utilise sont des paquets npm ou PyPI que le client lance sur la machine même de l’utilisateur (`npx -y …`, `uvx …`) : il n’y a aucun point de terminaison à contacter. HISTOR les observe sur un autre hôte qui installe le paquet et le lance sous [gVisor](https://gvisor.dev), un noyau écrit en espace utilisateur : le paquet parle à gVisor, pas au noyau de l’hôte. gVisor est un logiciel libre de Google sous [licence Apache 2.0](https://github.com/google/gvisor/blob/master/LICENSE) ; HISTOR l’exécute, il ne le distribue pas.

**Ce que fait une observation** (`histor/sandbox/histor_observe.py`, bibliothèque standard seulement) :

1. Installation, sous gVisor : npm avec `--ignore-scripts`, pip avec `--only-binary=:all:` — aucun code du paquet ne s’exécute. Un paquet PyPI qui ne publie qu’un sdist est signalé `no-wheel`, jamais construit.
2. Exécution, sous le runtime de traçage de gVisor (`runsc-trace`) : un réseau interne sans route vers l’extérieur dont le seul résolveur est le journal DNS de l’observateur, racine en lecture seule, copie propre et inscriptible du paquet, `/tmp` un tmpfs de 64 Mo, un uid sans privilèges, sans capabilities ni nouveaux privilèges, 512 Mo de mémoire sans swap, un CPU, 256 processus, aucun environnement hormis `PATH`/`HOME`. HOME contient des identifiants leurres (clés SSH, jetons cloud, de registres et Git, un portefeuille, un historique shell) et le répertoire de travail un `.env` leurre. L’observateur parle MCP par stdin/stdout : `initialize`, `tools/list`, puis chacun d’au plus 15 outils une fois avec des arguments canaris construits d’après son schéma (une adresse sur `histor-trap.invalid`, un chemin vers une note leurre) — dans le bac à sable, sans agir sur rien de réel — puis tue le conteneur. Les scripts d’installation npm, sautés à l’installation, s’exécutent d’abord à part, eux aussi tracés.
3. Réponse : la version, son empreinte d’intégrité, l’empreinte de l’image, un statut (`ok`, `exited` — le plus souvent un serveur qui a besoin d’une clé ou d’un chemin pour démarrer —, `install-failed`, `no-entry-point`, `no-wheel`, `timeout`, `protocol`) et les outils.

Si `runsc` n’est pas un runtime Docker, il refuse de s’exécuter plutôt que de se rabattre sur `runc`.

**Comportement.** gVisor écrit lui-même la trace de l’exécution (`execve`, `connect`, `open`/`openat`) hors du bac à sable : le paquet ne peut ni la cacher ni la falsifier ; le journal DNS note chaque nom demandé et répond par une adresse unique de 198.18.0.0/15 qui ne mène nulle part, si bien que chaque tentative de connexion est vue avec le nom visé. Par phase — scripts d’installation, démarrage, appels canaris — l’observation rapporte les programmes lancés, les noms demandés et les adresses composées, les leurres ouverts et les écritures qui survivraient hors du bac à sable (`.bashrc`, `authorized_keys`, …). L’activité du processus d’entrée dans la phase des scripts est celle de npm lui-même et est écartée, de même que les connexions loopback. L’installation ajoute le runtime `runsc-trace`, le réseau interne `histor-observe` (10.231.0.0/24), une règle de pare-feu qui ne lui laisse joindre que le résolveur, `CAP_NET_BIND_SERVICE` pour le service et une minuterie qui efface les traces lues. HISTOR relance une version quand son observateur est antérieur à `HISTOR_SANDBOX_OBSERVER` (2 = comportement).

**À qui appartient un enregistrement du piège.** Un enregistrement compte pour une exécution seulement si sa
connexion s’est ouverte pendant que le conteneur de cette exécution détenait l’adresse (depuis son `State.StartedAt`)
et s’il ne porte pas l’étiquette d’une autre exécution. Chaque leurre que HISTOR dépose ou transmet porte l’étiquette
de 12 caractères hexadécimaux de l’exécution (`canary-<étiquette>@histor-trap.invalid`,
`histor-canary-ssh-<étiquette>`, `histor-envkey-<étiquette>` …). Docker donne une adresse libérée au conteneur suivant,
et une connexion mise en file par le détenteur précédent peut être acceptée après le démarrage du suivant : un tel
enregistrement est montré comme le trafic d’une autre exécution et laisse l’exécution incomplète
(`foreign-run-traffic`), jamais comme destinataire, leurre ou destination de clé de cette exécution. Les raisons
d’incomplétude s’additionnent (`dns-unrecorded` et `foreign-run-traffic` ensemble) ; une exécution sans aucune est
propre, donc « l’observateur n’a pas pu rendre compte de toute l’exécution » ne se lit jamais comme « propre ».
Suggéré par un lecteur de l’étude des 12 915 serveurs ; observateur `gvisor-trace/8`.

**Quelles versions.** Une version publiée sur npm ou PyPI ne peut pas changer : chaque version est observée une fois. Chaque exploration demande aux registres la dernière version (c’est peu coûteux) et ne lance le bac à sable que pour les versions jamais vues, au plus `HISTOR_SANDBOX_MAX_PER_CRAWL` (300) : d’abord les nouvelles versions de paquets déjà observés — c’est là qu’une description d’outil modifiée atteint tous ceux qui lancent le paquet sans version fixée —, puis les paquets jamais observés. Une panne du bac à sable lui-même est enregistrée comme erreur interne d’HISTOR et n’émet aucune étiquette sur le paquet.

**Préparer l’hôte du bac à sable** (Ubuntu 24.04, Docker) :

```bash
# gVisor depuis le dépôt apt signé de Google, figé sur une version
curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
#   empreinte 6F1D F85E 3A71 C249 18E7  27D5 6FC6 D554 E32B D943 (The gVisor Authors)
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases 20261005 main" \
  > /etc/apt/sources.list.d/gvisor.list
apt-get update && apt-get install -y runsc
runsc install && systemctl reload docker      # un rechargement, pas un redémarrage : les conteneurs restent en marche

# l’observateur : utilisateur, réseau, images, TLS, jeton, unité systemd, règle de pare-feu pour le seul hôte d’HISTOR
HISTOR_CALLER_IP=<IP de l’hôte HISTOR> ./histor/sandbox/install.sh
```

`install.sh` affiche le SHA-256 du certificat et se termine par un auto-test qui doit indiquer `"gvisor":true` (le noyau à l’intérieur est `4.19.0-gvisor`). Le jeton est généré une fois dans `/etc/histor-sandbox.env` et jamais affiché ; copiez-le dans le `.env` de l’hôte HISTOR sans l’afficher. Sur l’hôte HISTOR :

```bash
HISTOR_SANDBOX_URL=https://<hôte du bac à sable>:9443
HISTOR_SANDBOX_TOKEN=<depuis /etc/histor-sandbox.env>
HISTOR_SANDBOX_CERT_SHA256=<affiché par install.sh>
```

HISTOR épingle le certificat : il vérifie l’empreinte avant d’envoyer le moindre octet, jeton compris. Deux pièges rencontrés pendant la mise en place : sur un réseau Docker défini par l’utilisateur, gVisor n’atteint pas le DNS intégré de Docker (`127.0.0.11`), donc l’étape d’installation reçoit son propre `resolv.conf` ; et le `--memory-swap` par défaut de Docker double la limite mémoire — une allocation de 900 Mo passait dans un conteneur de 512 Mo jusqu’à ce que le swap soit fixé égal à la mémoire. Journaux : `journalctl -u histor-sandbox`.

**Débit.** `HISTOR_SANDBOX_SLOTS` dans `/etc/histor-sandbox.env` (2 par défaut) est le nombre de paquets que l’hôte du bac à sable exécute à la fois ; chacun prend jusqu’à 1 Go pendant l’installation et 512 Mo pendant l’exécution. Réglez `HISTOR_SANDBOX_CONCURRENCY` d’HISTOR sur le même nombre. `HISTOR_SANDBOX_MAX_PER_CRAWL` plafonne les versions exécutées par exploration. Une observation complète prend 13 à 60 s : trois emplacements traitent environ 500 paquets par heure.

### Plusieurs hôtes de bac à sable

Un HISTOR répartit les paquets sur au plus **8** hôtes de bac à sable. Le premier se règle avec `HISTOR_SANDBOX_URL` / `_TOKEN` / `_CERT_SHA256` et `HISTOR_SANDBOX_CONCURRENCY` ; les autres avec `HISTOR_SANDBOX_2_URL` / `_TOKEN` / `_CERT_SHA256` / `_CONCURRENCY` … jusqu’à `HISTOR_SANDBOX_8_*`. Un paquet va à l’hôte qui a un emplacement libre, à tour de rôle, si bien qu’un hôte plus rapide en prend simplement davantage. Quand un hôte tombe en panne en tant qu’infrastructure — injoignable, mauvais certificat, `busy`, gVisor absent — le paquet est tenté une fois sur un autre hôte et l’hôte défaillant se repose 60 s : il n’est repris plus tôt que si aucun autre n’a d’emplacement libre. Le résultat propre d’un paquet (`exited`, `install-failed`, `timeout`, …) n’est jamais retenté ailleurs : tous les hôtes exécutent le même observateur, un autre dirait la même chose. Ce n’est que si les deux tentatives échouent que la panne est enregistrée comme celle d’HISTOR (`sandbox-error`), jamais comme une étiquette sur le paquet. Les questions aux registres (dernière version, provenance, éditeur) sont posées par HISTOR lui-même, pas par un bac à sable.

**Les limites, et pourquoi.**

| Limite | Valeur | Pourquoi |
|---|---|---|
| Hôtes | 8 : `HISTOR_SANDBOX_URL`, puis `_2_` … `_8_` | Chaque hôte exécute le code d’inconnus et garde son propre pare-feu vérifié, son jeton et son certificat, sur la même version de gVisor et de l’observateur ; au-delà de huit, cet entretien coûte plus que le débit n’apporte. `HISTOR_SANDBOX_9_URL` empêche le démarrage. |
| Emplacements par hôte | 1–8 : `HISTOR_SANDBOX_N_CONCURRENCY`, égal au `HISTOR_SANDBOX_SLOTS` de cet hôte | Un emplacement prend jusqu’à 1 Go pendant l’installation, puis 512 Mo et un CPU pendant l’exécution : huit emplacements demandent déjà une machine de 16 cœurs et 16 Go. Un nombre plus grand est ramené à 8. |
| En cours, tous hôtes confondus | 12 par défaut : `HISTOR_SANDBOX_MAX_SLOTS`, au plus 32 | Chaque observation occupe l’un des workers d’exploration d’HISTOR (`HISTOR_CRAWL_WORKERS`, 12) jusqu’à `HISTOR_SANDBOX_TIMEOUT_S` et peut mettre en mémoire une réponse de 32 Mio dans le conteneur de 768 Mo d’HISTOR. Douze emplacements à 13–60 s par paquet traitent environ 700 à 3 300 paquets par heure — toute la liste curée d’environ 40 000 en un à deux jours. Dépasser 12 impose de relever aussi `HISTOR_CRAWL_WORKERS` et `HISTOR_MEM_LIMIT`. |

HISTOR refuse aussi de démarrer avec un hôte à moitié configuré (une URL sans son jeton ou son empreinte), avec des hôtes supplémentaires sans le premier, ou avec la même URL deux fois : un hôte écarté en silence ressemblerait à une exploration lente, pas à une faute de frappe.

**Ajouter un hôte de bac à sable, dans l’ordre.**

1. **Vérifier la machine.** Ubuntu 24.04 avec Docker sur x86-64 ; par emplacement, environ 1,5 Go de mémoire libre et un cœur. L’observateur a besoin pour lui seul du sous-réseau `10.231.0.0/24` et des ports 9443, 18025, 18080, 18443 et 18587 — les deux commandes ne doivent rien afficher :
   ```bash
   ip route | grep '10\.231\.'
   ss -ltn | grep -E ':(9443|18025|18080|18443|18587)\b'
   ```
2. **Installer gVisor** depuis le dépôt de Google, épinglé à la même version que sur les autres hôtes (voir « Préparer l’hôte du bac à sable » plus haut), puis `runsc install && systemctl reload docker`.
3. **Copier l’observateur depuis le même commit que les autres hôtes.** Chaque hôte doit exécuter la version de l’observateur qu’attend HISTOR (`HISTOR_SANDBOX_OBSERVER`) : un hôte sur une autre version donnerait d’autres preuves pour ses paquets.
   ```bash
   rsync -a histor/sandbox/ <hôte du bac à sable>:/opt/histor-sandbox/
   ```
4. **Installer en root**, en n’ouvrant le 9443 qu’à l’hôte d’HISTOR :
   ```bash
   sudo HISTOR_CALLER_IP=<IP de l’hôte d’HISTOR> /opt/histor-sandbox/install.sh
   ```
   Il affiche le SHA-256 du certificat et se termine par un auto-test qui doit indiquer `"gvisor":true`. Sans ufw, ouvrez le 9443 à la seule IP d’HISTOR dans le pare-feu de l’hôte ou du fournisseur.
5. **Le dimensionner.** Réglez `HISTOR_SANDBOX_SLOTS` dans `/etc/histor-sandbox.env` selon la machine, puis `systemctl restart histor-sandbox`.
6. **Confier l’hôte à HISTOR**, dans son `.env`, sous le prochain numéro libre. Chaque hôte a son propre jeton ; déplacez-le sans l’afficher :
   ```bash
   ssh <hôte du bac à sable> "sudo sed -n 's/^HISTOR_SANDBOX_TOKEN=/HISTOR_SANDBOX_2_TOKEN=/p' /etc/histor-sandbox.env" \
     | ssh <hôte d’HISTOR> 'cat >> /opt/histor/.env'
   ```
   et ajoutez le reste à la main :
   ```bash
   HISTOR_SANDBOX_2_URL=https://<IP de l’hôte du bac à sable>:9443
   HISTOR_SANDBOX_2_CERT_SHA256=<affiché par install.sh>
   HISTOR_SANDBOX_2_CONCURRENCY=<HISTOR_SANDBOX_SLOTS de cet hôte>
   ```
   Avec plus d’emplacements, relevez aussi `HISTOR_SANDBOX_MAX_PER_CRAWL` — sinon une exploration observe le même nombre de paquets, seulement plus tôt.
7. **Redémarrer HISTOR avec le nouveau `.env` :** `./scripts/deploy_histor.sh --remote <hôte d’HISTOR>`. Il recrée le conteneur (le `.env` de l’hôte est conservé) et relance l’exploration que le redémarrage a interrompue ; ce qu’elle a déjà validé reste, et les versions pas encore observées passent ensuite.
8. **Vérifier chaque hôte depuis HISTOR**, par le même canal épinglé que l’exploration :
   ```bash
   docker compose -p histor exec -T histor python -m histor sandboxes
   ```
   Une ligne par hôte : numéro, adresse, emplacements, observateur, `gvisor`, `trace`, `observeNetwork`, `ok`. Le code de sortie est non nul si un hôte n’est pas prêt — injoignable, mauvaise empreinte ou mauvais jeton, autre version de l’observateur. Les jetons ne sont jamais affichés.
9. **Suivre la première exploration.** `/api/v1/stats` montre `crawl.sandboxHosts` (hôtes configurés) puis, à la fin de l’exploration, `lastRun.sandboxHosts` : par numéro d’hôte, combien de paquets chacun a observés et combien de fois il a échoué. Sur un hôte du bac à sable : `journalctl -u histor-sandbox`.

**Observer une cible à nouveau, maintenant :** `docker compose -p histor exec -T histor python -m histor reobserve pypi:ipinfo-mcp-server` (une ou plusieurs adresses ou ids). Le chemin est celui d’un parcours — bac à sable ou client MCP, WARDEN, l’écriture, la chaîne d’étiquettes, un événement de sécurité — et l’observation est rattachée au parcours en cours, sinon au dernier, pour qu’un parcours d’une seule cible ne devienne pas le dernier parcours public. Un paquet reçoit ce que son enregistrement dit qu’il a demandé (valeurs factices, argument de démarrage, environnement). Elle sert à remplacer un enregistrement faux aujourd’hui sans attendre qu’un parcours l’atteigne ; l’observation précédente reste dans l’historique.

**Nouvelle version de l’observateur :** mettez d’abord à jour tous les hôtes (`install.sh` à nouveau depuis le nouveau commit — il est idempotent), vérifiez avec `histor sandboxes`, et seulement ensuite relevez `HISTOR_SANDBOX_OBSERVER` côté HISTOR. `HISTOR_SANDBOX_OBSERVERS_VALID` liste les observateurs antérieurs dont les observations ok et complètes comptent encore (par défaut `3` : l’observateur 4 a changé la façon de lancer un paquet qui échoue, pas ce que rapporte un paquet qui tourne) ; leurs échecs sont relancés avant les paquets jamais observés, avec au moins la moitié d’un parcours. Une page ok et complète tracée par un lecteur antérieur à `gvisor-trace/9` (qui ne voyait ni renommages ni liens) est tracée une fois de plus et partage cette moitié avec les échecs. **Retirer un hôte :** supprimez ses quatre lignes et redémarrez HISTOR ; les numéros n’ont pas à se suivre.
