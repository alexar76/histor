# Contrastes con otros observadores

[English](cross-check.md) · [Русский](cross-check.ru.md) · [Español](cross-check.es.md) · [Français](cross-check.fr.md) · [中文](cross-check.zh.md)

HISTOR no es el único servicio que lee las listas de herramientas de los servidores del MCP Registry. Los
observadores que leen los mismos servidores de forma independiente se comprueban mutuamente: cuando discrepan, un
servidor les respondió distinto (según el cliente, la región o el momento) o uno de ellos tiene un error. HISTOR
contrasta sus registros con esos observadores según un calendario y publica cada contraste, desacuerdos incluidos,
en https://histor.modelmarket.dev/datasets/peers/.

## Los observadores

| Observador | Método | Calendario | Publicado en | Desde |
|---|---|---|---|---|
| CrawlCheck (crawlcheck.io) | resúmenes (digests) de conjuntos de herramientas | cada día, 12:00 UTC | `/datasets/peers/crawlcheck/` | 2026-11-07 |
| ScraperWatch (scraperwatch.com) | cambios por campo | cada día, 12:00 UTC | `/datasets/peers/scraperwatch/` | 2026-11-01 |

La lista es un único archivo de configuración, `/etc/histor/peers.json` en el host (`deploy/peers.json` en este
repositorio). Su forma pública es https://histor.modelmarket.dev/datasets/peers/index.json: cada observador, su
método, fuentes y calendario, y el día más reciente contrastado. Cada día es una carpeta,
`/datasets/peers/<observador>/<AAAA-MM-DD>/`, y `latest/` apunta a la más reciente. `/datasets/xcheck-crawlcheck/`,
la dirección usada antes de que existiera la lista, lleva al mismo sitio.

## Añadir un observador

Un observador que publique resúmenes de conjuntos de herramientas MTL/1 (ver abajo) solo necesita una entrada en la
lista:

```json
{
  "slug": "example",
  "name": "Example Observer",
  "contact": "ops@example.org",
  "method": "digest",
  "window_hours": 24,
  "sources": [{"name": "list", "url": "https://example.org/histor-list.jsonl"}]
}
```

Una fuente es cualquier archivo que el observador publique por https con una fila por lectura de servidor: `name` (el
nombre en el registro), `endpoint`, `digest` (SRI o hex) y `read_at` (RFC 3339), en JSON, JSON Lines o CSV. El feed
de cambios de CrawlCheck se lee tal como se publica. Una fila con `digest` null y un campo `outcome` indica que el
observador no pudo leer ese servidor. `histor-peers.timer` ejecuta cada día todas las entradas
(`deploy/peers_check.sh`, `histor/peers.py`); véase [operations.es.md](operations.es.md). Un observador con un registro de cambios por campo en el formato de ScraperWatch se añade con el método `field-diff` y los parámetros que muestra la entrada de ScraperWatch en `deploy/peers.json`.

## Método: resúmenes de conjuntos de herramientas

Es el resumen del conjunto de herramientas de MTL/1 ([labels.es.md](labels.es.md)). Para cada herramienta: `name`,
`description` (`""` si falta), `inputSchema` (`{}` si falta) y `outputSchema` solo cuando el servidor envió un
objeto; herramientas ordenadas por nombre en unidades de código UTF-16; JSON canónico según RFC 8785; SHA-256 escrito
como cadena SRI. En su especificación de lockfile, CrawlCheck lo llama `jcs_v1.tools_sha256`
(https://crawlcheck.io/spec/mcp-lockfile). Comprobado el 7 de noviembre de 2026: los dos vectores de prueba de
CrawlCheck, seis conjuntos de los registros de HISTOR calculados por el servicio de CrawlCheck y el propio servidor de
CrawlCheck dan resúmenes iguales en ambos lados.

Ambos servicios incluyen `annotations`, `title` e `icons` en un resumen de extras separado. El 7 de noviembre de 2026,
[CrawlCheck informó](https://github.com/modelcontextprotocol/registry/issues/82) de 30 coincidencias en los extras,
entre ellas `sha256-G44DGgW7…` para crawlcheck.io/mcp. El resultado se refiere a esas lecturas. Las comparaciones de
extras dependen de la codificación y los límites de almacenamiento. La ejecución diaria compara solo el resumen del
conjunto de herramientas, porque ninguna de las fuentes lleva un resumen de extras.

Cada fila publicada por el observador se compara con el historial de HISTOR para ese servidor (`/api/v1/servers/<id>`):

| Veredicto | Significado |
|---|---|
| `agree` | el resumen de HISTOR en el momento de la otra lectura es el mismo (entre dos lecturas de HISTOR, vale cualquiera de las dos) |
| `agree_nearby` | no es el que HISTOR tenía en ese momento, pero HISTOR lo vio dentro de la ventana: el servidor cambió entre las lecturas |
| `disagree` | HISTOR tenía resúmenes dentro de la ventana y nunca este |
| `histor_no_digest` | HISTOR no tiene resumen del servidor dentro de la ventana (no lo leyó, o una lista que MTL/1 no puede resumir) |
| `not_listed` | HISTOR no registra este nombre y esta dirección |
| `theirs_no_digest` | el otro observador no pudo leer la lista (`digest` es null y `outcome` dice por qué) |
| `bad_row` | falta un campo o no se puede leer |

La ventana, de 24 horas por defecto, hace falta porque algunos servidores cambian su lista en cada lectura: el 7 de
noviembre, cuatro de los siete servidores probados obtenían un resumen nuevo cada 30 minutos aproximadamente. Dos
observadores entonces solo coinciden «cerca».

Para CrawlCheck la ejecución lee dos fuentes: su feed público de cambios (https://crawlcheck.io/api/v1/mcp/changes,
los 500 eventos más recientes, unos dos días y medio, por eso la ejecución es diaria) y su lista compartida semanal
de 30 servidores, releída cada lunes antes de las 11:30 UTC (https://crawlcheck.io/data/xcheck/histor/latest.jsonl,
ediciones fechadas con una declaración firmada al lado). La carpeta de cada día guarda, por fuente, el archivo tal
como se leyó (`<fuente>.input.*`), la comparación (`<fuente>.report.json`, `<fuente>.report.md`) y `SHA256SUMS`.

Para ejecutar el contraste uno mismo, con Python 3.11 o posterior y sin instalar nada:

```bash
python3 scripts/xcheck_digests.py https://crawlcheck.io/data/xcheck/histor/latest.jsonl --out report.json --md report.md
```

Código de salida 0: ningún `disagree`; 1: al menos uno; 2: no se pudo leer la entrada.

Primeros resultados, 7 de noviembre de 2026: la primera edición de la lista compartida (sha256 `9271b8ac…`), 30
servidores, 30 `agree`; el feed de cambios, 16 filas con el resumen, 15 `agree`, 1 `histor_no_digest`, 0 `disagree`.

## Método: cambios por campo

ScraperWatch publica un registro diario de cambios de herramientas en un panel fijo de endpoints remotos, como rutas
JSON con valores antiguos y nuevos. Cada día HISTOR contrasta el día más reciente de ese registro en ambos sentidos:
cada cambio de su registro frente a los registros de HISTOR ruta por ruta, y cada cambio de HISTOR en su panel que su
registro no recoge, con su motivo (`histor/peer_xcheck.py`, [operations.es.md](operations.es.md)). Lee la base de datos de HISTOR, así que se ejecuta en su propio contenedor con la configuración de la base. Sus
parámetros (direcciones del registro y del panel, agente de robots.txt, hora y primera ejecución) están en su entrada
de la lista. Hasta el 7 de noviembre de 2026 se ejecutaba cada semana, tras la exportación del dataset.

## Un cuarto testigo

Desde el 7 de noviembre de 2026, CrawlCheck también firma como testigo los checkpoints de HISTOR
(c2sp.org/tlog-witness), desde su propia red: `crawlcheck.io/witness`, vkey
`crawlcheck.io/witness+f6b056e2+BCYE6WcaJVekqxEETT9IF/pKvxP7yZM6dLW65YDWpQpE`. Su cofirma aparece en
https://histor.modelmarket.dev/log/checkpoint junto a las de los tres testigos que ya firmaban antes.
