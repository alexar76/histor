# Contraste con otro observador

[English](cross-check.md) · [Русский](cross-check.ru.md) · [Español](cross-check.es.md) · [Français](cross-check.fr.md) · [中文](cross-check.zh.md)

HISTOR no es el único servicio que lee las listas de herramientas de los servidores del MCP Registry. CrawlCheck
(crawlcheck.io) también las lee, y desde el 7 de noviembre de 2026 publica el mismo resumen (digest) del conjunto de
herramientas que usa HISTOR. Dos observadores que leen un servidor más o menos al mismo tiempo y calculan el resumen
de forma independiente se comprueban mutuamente: un desacuerdo significa que a uno de ellos el servidor le entregó
otra cosa (por ejemplo, responde distinto según el cliente o la región) o que uno de ellos tiene un error.

## El resumen

Es el resumen del conjunto de herramientas de MTL/1 ([labels.es.md](labels.es.md)). Para cada herramienta: `name`,
`description` (`""` si falta), `inputSchema` (`{}` si falta) y `outputSchema` solo cuando el servidor envió un
objeto; herramientas ordenadas por nombre en unidades de código UTF-16; JSON canónico según RFC 8785; SHA-256 escrito
como cadena SRI. En su especificación de lockfile, CrawlCheck lo llama `jcs_v1.tools_sha256`
(https://crawlcheck.io/spec/mcp-lockfile).

Comprobado el 7 de noviembre de 2026: los dos vectores de prueba publicados por CrawlCheck, seis conjuntos de
herramientas de los registros de HISTOR calculados por el servicio de CrawlCheck y el propio servidor de CrawlCheck
dan resúmenes iguales en ambos lados. `annotations`, `title` e `icons` quedan fuera de este resumen. Ambos servicios
los guardan bajo un segundo resumen, construido de forma distinta, así que esos dos no coinciden y no se comparan.

## La ejecución diaria

Cada día a las 12:00 UTC, HISTOR lee el feed público de cambios de CrawlCheck
(https://crawlcheck.io/api/v1/mcp/changes, sus 500 eventos más recientes, unos dos días y medio) y compara cada
evento que lleva el resumen con el historial de HISTOR para ese servidor (`/api/v1/servers/<id>`). El resultado se
publica en https://histor.modelmarket.dev/datasets/xcheck-crawlcheck/ en una carpeta por día: el feed tal como se
leyó (`feed.input.json`), la comparación (`feed.report.json`, `feed.report.md`) y `SHA256SUMS`. `latest/` apunta al
día más reciente. Los desacuerdos se publican tal cual, sin filtrar.

| Veredicto | Significado |
|---|---|
| `agree` | el resumen de HISTOR en el momento de la otra lectura es el mismo (entre dos lecturas de HISTOR, vale cualquiera de las dos) |
| `agree_nearby` | no es el que HISTOR tenía en ese momento, pero HISTOR lo vio dentro de 24 horas: el servidor cambió entre las lecturas |
| `disagree` | HISTOR tenía resúmenes dentro de 24 horas y nunca este |
| `histor_no_digest` | HISTOR no tiene resumen del servidor dentro de 24 horas (no lo leyó, o una lista que MTL/1 no puede resumir) |
| `not_listed` | HISTOR no registra este nombre y esta dirección |
| `bad_row` | falta un campo o no se puede leer |

La ventana de 24 horas hace falta porque algunos servidores cambian su lista en cada lectura: el 7 de noviembre,
cuatro de los siete servidores probados obtenían un resumen nuevo cada 30 minutos aproximadamente. Dos observadores
entonces solo coinciden «cerca».

## Ejecutarlo uno mismo

```bash
python3 scripts/xcheck_digests.py https://crawlcheck.io/api/v1/mcp/changes --out report.json --md report.md
```

Python 3.11 o posterior, solo la biblioteca estándar. La entrada es un archivo o una URL https: el feed de cambios de
CrawlCheck tal como se publica, o cualquier lista de filas con `name` (el nombre en el registro), `endpoint`, `digest`
(SRI o hex) y `read_at` (RFC 3339) en JSON, JSON Lines o CSV. Código de salida 0: ningún `disagree`; 1: al menos uno;
2: no se pudo leer la entrada.

## Primer resultado

7 de noviembre de 2026, la primera ejecución sobre el feed: 8 eventos con el resumen, 7 `agree`, 1
`histor_no_digest`, 0 `disagree`.
