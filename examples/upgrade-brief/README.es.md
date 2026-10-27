# Reproducir un análisis de actualizaciones MCP con HISTOR

[English](README.md) · [Русский](README.ru.md) · [Español](README.es.md) · [Français](README.fr.md) · [中文](README.zh.md)

Este ejemplo compara seis observaciones sintéticas guardadas de tres dependencias. Relaciona los
cambios de definición con cuatro flujos de trabajo y propone pruebas concretas antes de actualizar.
Los datos de prueba no describen paquetes publicados, endpoints activos ni despliegues de clientes.

## Ejecutar desde el código fuente

Use Python 3.11 o posterior desde `histor/`. No necesita instalación, credenciales, modelo ni red:

```bash
python3 -m histor upgrade-brief \
  --lang es \
  --watchlist examples/upgrade-brief/watchlist.es.json \
  --out-dir ../output/histor-upgrade-brief-example/es
python3 examples/upgrade-brief/verify.py \
  --out-dir ../output/histor-upgrade-brief-example
```

Use `.venv/bin/python` en lugar de `python3` si es su intérprete Python 3.11+. El primer comando
escribe `report.md` y `report.json`. El segundo verifica las cinco listas de idiomas dos veces
contra [expected.json](expected.json), comprueba que los resultados repetidos son idénticos y
las entradas permanecen intactas, muestra `PASS` y termina con `0`. Una comprobación fallida
termina con `1`. Los informes se guardan en `en/`, `ru/`, `es/`, `fr/` y `zh/`; repetir la ejecución
sustituye los informes generados.

El verificador también ejecuta dos veces el comando sin `--lang` y exige el mismo resultado que con `--lang en`: 12 ejecuciones sin conexión en total. El `watchlist.json` principal está en inglés; `watchlist.en.json` sigue siendo un alias equivalente.

## Resultados esperados

El resumen es `changed: 1`, `unchanged: 0`, `context_changed: 1`, `unknown: 1`.

| Dependencia | Resultado | Resultado de los flujos |
|---|---|---|
| `demo:research` | `changed`, definiciones comparables | `weekly-review` y `archive-import`: `retest` |
| `demo:migration` | `context_changed`, cambió el protocolo | `status-check`: `unknown` |
| `demo:offline` | `unknown`, falló la nueva observación | `daily-status`: `unknown` |

Para `weekly-review`, repita una petición guardada de `search_docs` con el nuevo campo obligatorio
`workspace`. Compruebe que `limit` ahora se declara como cadena y adapte la lectura del resultado
de `items` a `results`. Lea la descripción modificada y confirme la selección de herramientas
esperada. `export_brief` cambia `readOnlyHint` de `true` a `false` y `destructiveHint` de `false`
a `true`; revise la gestión de confirmaciones y permisos antes de probar una exportación. Estos
hints son declaraciones del servidor.

Para `archive-import`, se elimina la herramienta seleccionada `legacy_fetch`. La nueva herramienta
no seleccionada `fetch_document` acepta `id` en lugar de `document_id`. Revise esa candidata,
actualice explícitamente la selección del flujo si corresponde y pruebe un documento conocido;
el análisis no la declara un reemplazo automático.

Para `status-check`, las definiciones de `read_status` no cambian, mientras el protocolo pasa de
`2025-06-18` a `2026-07-28`. Repita las observaciones con un cliente que admita el protocolo previsto,
manteniendo las mismas condiciones de arranque y acceso. Para `daily-status`, obtenga una nueva
observación completa y correcta antes de comparar las herramientas. Una captura fallida no
demuestra la eliminación de herramientas.

El verificador comprueba las definiciones y la estructura esperada del informe. Las pruebas de
estos flujos son instrucciones para su propio entorno de pruebas; el ejemplo no ejecuta ningún
servidor ni herramienta MCP.

## Variantes de idioma y datos propios

| Idioma | Lista de seguimiento |
|---|---|
| English | [watchlist.json](watchlist.json) |
| Русский | [watchlist.ru.json](watchlist.ru.json) |
| Español | [watchlist.es.json](watchlist.es.json) |
| Français | [watchlist.fr.json](watchlist.fr.json) |
| 中文 | [watchlist.zh.json](watchlist.zh.json) |

Las variantes traducen títulos y descripciones de flujos y comparten instantáneas, ID y herramientas
seleccionadas. `--lang ru|en|es|fr|zh` elige las explicaciones y listas de comprobación del informe,
incluidos los textos y límites en JSON. El valor predeterminado es inglés (`en`). Los nombres de campos,
códigos de estado, motivos de diagnóstico, ID, esquemas y hashes son estables; el campo JSON
`language` registra la elección. Sus títulos, descripciones de flujos y fragmentos de definiciones
se conservan: combine la opción con la lista localizada correspondiente; los datos de entrada
no se traducen automáticamente.

Para usar sus datos, copie el conjunto de archivos en otro directorio, sustituya las instantáneas
y edite las selecciones de herramientas y los flujos. `expected.json` y `verify.py` describen este
ejemplo sintético fijo y necesitarán otras expectativas para datos nuevos. El comando independiente
`capture` permite guardar observaciones HTTP con acceso a la red; consulte la [guía](../../docs/upgrade-brief.es.md).
