# Especificación del formato del log de transparencia HISTOR (`histor-tlog/2`) — resumen

> El texto normativo es solo el inglés: [transparency-log.md](transparency-log.md). Este resumen no es normativo. Otros idiomas: [English](transparency-log.md) · [Русский](README.ru.md) · [Français](README.fr.md) · [中文](README.zh.md).

Qué fija la especificación:

- **La hoja** del árbol es una etiqueta: una W3C Verifiable Credential del perfil MTL/1, firmada por la clave del log (`did:key`, Ed25519, cryptosuite `eddsa-jcs-2022`). La entrada de la hoja son los bytes canónicos RFC 8785 del documento *tal como se sirve*, prueba incluida. `leaf_hash = SHA-256(0x00 ‖ JCS(label))`.
- **El árbol** es RFC 9162 (Certificate Transparency v2): `node_hash = SHA-256(0x01 ‖ left ‖ right)`; pruebas de inclusión y de consistencia sin cambios.
- **La cabeza firmada** (`histor.sth/v1`): `log`, `treeSize`, `rootHash`, `timestamp`; firma Ed25519 sobre los bytes canónicos de todos los miembros salvo `signature`, con `type` dentro de lo firmado, así que un documento nunca puede hacerse pasar por otro. Una cabeza por tamaño de árbol, para siempre.
- **Regla de verificación**: `type` se comprueba primero; solo vale la clave fijada (pinned) del log; se rechazan claves y puntos de orden pequeño antes de evaluar la ecuación (regla Ed25519 estricta).
- **La respuesta de `/check`** (`histor.check/v1`) es una afirmación firmada de que el log guarda ese veredicto para ese conjunto de herramientas: una pista que comprobar, no una prueba.
- **Lo que no hay**: firmas de editores (el lado del editor es una declaración de conjuntos en el registro o en un archivo de su dominio), pruebas de tiempo ni de completitud.
- **`histor-tlog/2` (30.10.2026):** la misma cabeza se sirve como **checkpoint C2SP** (`GET /checkpoint`: origen, tamaño, raíz en base64, firma del log según c2sp.org/signed-note y después las líneas de cofirma de testigos según c2sp.org/tlog-cosignature), y la instancia es un **testigo** según c2sp.org/tlog-witness para los logs listados en `HISTOR_WITNESS_LOGS` (`POST /witness/v1/add-checkpoint`, clave propia `witness.key`). Árbol, hojas y pruebas no cambian. Los vectores se verificaron con las bibliotecas Go de referencia (`golang.org/x/mod/sumdb/note`, `github.com/transparency-dev/formats/note`).
- **Vectores** `test-vectors/vectors.json`, normativos: 8 hojas, raíces para tamaños 1–8, todas las pruebas de inclusión y consistencia, dos cabezas firmadas con la clave de prueba, tres casos negativos. `tests/test_spec_vectors.py` mantiene el código de acuerdo con el archivo.
