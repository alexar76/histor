# Spécification du format du journal de transparence HISTOR (`histor-tlog/2`) — résumé

> Seul le texte anglais est normatif : [transparency-log.md](transparency-log.md). Ce résumé ne l'est pas. Autres langues : [English](transparency-log.md) · [Русский](README.ru.md) · [Español](README.es.md) · [中文](README.zh.md).

Ce que la spécification fixe :

- **La feuille** de l'arbre est un label : une W3C Verifiable Credential du profil MTL/1, signée par la clé du journal (`did:key`, Ed25519, cryptosuite `eddsa-jcs-2022`). L'entrée de la feuille est constituée des octets canoniques RFC 8785 du document *tel qu'il est servi*, preuve comprise. `leaf_hash = SHA-256(0x00 ‖ JCS(label))`.
- **L'arbre** est celui de la RFC 9162 (Certificate Transparency v2) : `node_hash = SHA-256(0x01 ‖ left ‖ right)` ; preuves d'inclusion et de cohérence inchangées.
- **La tête signée** (`histor.sth/v1`) : `log`, `treeSize`, `rootHash`, `timestamp` ; signature Ed25519 sur les octets canoniques de tous les membres sauf `signature`, `type` étant dans les octets signés : un document ne peut jamais se faire passer pour un autre. Une tête par taille d'arbre, pour toujours.
- **Règle de vérification** : `type` est vérifié d'abord ; seule la clé épinglée (pinned) du journal compte ; les clés et points d'ordre petit sont rejetés avant l'équation (règle Ed25519 stricte).
- **La réponse de `/check`** (`histor.check/v1`) est une affirmation signée que le journal détient ce verdict pour cet ensemble d'outils : une piste à vérifier, pas une preuve.
- **Ce qui n'y est pas** : signatures d'éditeurs (côté éditeur, une déclaration d'ensembles dans le registre ou dans un fichier sur son domaine), preuves de temps ou de complétude.
- **`histor-tlog/2` (30.10.2026) :** la même tête est servie comme **checkpoint C2SP** (`GET /checkpoint` : origine, taille, racine en base64, signature du journal selon c2sp.org/signed-note, puis les lignes de cosignature des témoins selon c2sp.org/tlog-cosignature), et l'instance est un **témoin** selon c2sp.org/tlog-witness pour les journaux listés dans `HISTOR_WITNESS_LOGS` (`POST /witness/v1/add-checkpoint`, clé propre `witness.key`). Arbre, feuilles et preuves ne changent pas. Les vecteurs sont vérifiés par les bibliothèques Go de référence (`golang.org/x/mod/sumdb/note`, `github.com/transparency-dev/formats/note`).
- **Vecteurs** `test-vectors/vectors.json`, normatifs : 8 feuilles, racines pour les tailles 1–8, toutes les preuves d'inclusion et de cohérence, deux têtes signées avec la clé de test, trois cas négatifs. `tests/test_spec_vectors.py` garde le code en accord avec le fichier.
