# Guía de análisis de actualizaciones MCP con HISTOR

[English](upgrade-brief.md) · [Русский](upgrade-brief.ru.md) · [Español](upgrade-brief.es.md) · [Français](upgrade-brief.fr.md) · [中文](upgrade-brief.zh.md)

El prototipo local `histor upgrade-brief` relaciona los cambios en las definiciones de herramientas
MCP con sus flujos de trabajo. Proporcione dos observaciones guardadas por dependencia y una selección
explícita de herramientas. Genera `report.md` con comprobaciones sugeridas y `report.json` con las
diferencias, versiones, límites de comparación y hashes SHA-256 de los archivos de entrada.

Use la copia local actual del código fuente para este prototipo. El informe se genera de forma
determinista y sin conexión, sin LLM, base de datos, credenciales ni configuración de producción.
Las descripciones de herramientas se tratan como datos. Un cambio de definición es motivo para
volver a probar un flujo; por sí solo no demuestra un cambio de comportamiento ni un fallo de la actualización.

## Ejecutar y verificar el ejemplo

Necesita Python 3.11 o posterior. Desde el directorio `histor/` de este espacio de trabajo, ejecute:

```bash
python3 --version
python3 -m histor upgrade-brief \
  --lang es \
  --watchlist examples/upgrade-brief/watchlist.es.json \
  --out-dir ../output/histor-upgrade-brief-example/es
python3 examples/upgrade-brief/verify.py \
  --out-dir ../output/histor-upgrade-brief-example
```

Si su intérprete compatible es `.venv/bin/python`, úselo en lugar de `python3` en cada comando.
El ejemplo sin conexión no requiere instalar paquetes. El verificador ejecuta las cinco listas
de seguimiento dos veces, comprueba los cambios y estados de flujo esperados, verifica que los
resultados se repiten byte por byte y confirma que los archivos de entrada no cambiaron. Si todo
es correcto, muestra `PASS` y termina con `0`; una comprobación fallida termina con `1`. Cada
directorio de idioma contiene `report.md` y `report.json`. Repetir los comandos sustituye esos informes.

El verificador también ejecuta dos veces el comando sin `--lang` y exige el mismo resultado que con `--lang en`: 12 ejecuciones sin conexión en total.

Las seis observaciones son sintéticas. No se inicia ningún servidor MCP ni se llama a herramientas.
El [ejemplo explicado](../examples/upgrade-brief/README.es.md) detalla los resultados esperados y
las pruebas concretas. Las instrucciones, los títulos de las listas y las descripciones de flujos están disponibles
en cinco idiomas.

## Elegir el idioma del informe

`--lang` acepta `ru`, `en`, `es`, `fr` o `zh`. El valor predeterminado es inglés (`en`) en la CLI, la API de Python y el renderizador cuando falta el campo `language`. Selecciona el idioma de los encabezados y explicaciones
propios de Markdown, las comprobaciones de herramientas y flujos, y los textos de comprobaciones
y límites en JSON. El campo JSON `language` registra la elección. Los nombres de campos, códigos
de estado, motivos de diagnóstico, ID, esquemas y hashes no cambian.

Los títulos de las listas, las descripciones de flujos y los fragmentos de las definiciones se
conservan tal como se proporcionan. Elija la lista localizada del ejemplo para las etiquetas en
el idioma deseado; `--lang` no traduce automáticamente los datos de entrada.

## Seleccionar dependencias y flujos

Cree un directorio con `watchlist.json` y `snapshots/`. Las rutas de las instantáneas son relativas
al directorio de la lista y deben permanecer dentro de él. Se rechazan URL, rutas absolutas,
componentes `..` y enlaces simbólicos que apunten fuera del directorio.

```json
{
  "format": "histor.upgrade-watchlist/v1",
  "title": "Nuestro flujo de investigación",
  "dependencies": [{
    "id": "our-research-server",
    "before": "snapshots/before.json",
    "after": "snapshots/after.json",
    "tools": ["search_docs"],
    "workflows": [{
      "id": "weekly-review",
      "description": "Preparar la revisión semanal de documentos",
      "tools": ["search_docs"]
    }]
  }]
}
```

Cada dependencia necesita una selección no vacía de herramientas y al menos un flujo. Un flujo
solo puede utilizar herramientas seleccionadas para esa dependencia. Mantenga estables los ID
de dependencias, nombres de herramientas e ID de flujos al traducir las etiquetas. `before` o
`after` pueden ser `null`; una observación ausente deja la comparación como desconocida. Para
comparar, el `targetId` de cada instantánea debe coincidir con el `id` de la dependencia.

Ejecute su conjunto de archivos desde `histor/`:

```bash
python3 -m histor upgrade-brief \
  --lang es \
  --watchlist /path/to/bundle/watchlist.json \
  --out-dir /path/to/report
```

## Guardar una observación HTTP

El comando independiente `capture` realiza peticiones de red al endpoint indicado. Requiere las
dependencias de ejecución de HISTOR declaradas en `pyproject.toml`. Si no están instaladas, prepare
primero un entorno desde esta copia del código; la instalación puede acceder a índices de paquetes:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Capture un endpoint HTTP MCP público con su URL real y un nombre de archivo de salida nuevo:

```bash
.venv/bin/python -m histor upgrade-brief capture \
  --url https://your-public-server.example/mcp \
  --target-id our-research-server \
  --out /path/to/bundle/snapshots/before.json
```

La URL `.example` y las rutas `/path/to/` son marcadores de posición. Repita después de actualizar
con `after.json` y genere el informe sin conexión. La captura usa discovery, inicialización legacy
cuando es necesaria y todas las páginas de `tools/list`. Nunca llama a herramientas ni instala
o inicia paquetes MCP. No admite autenticación del endpoint y rechaza URL con userinfo, consulta
o fragmento. Las direcciones privadas se bloquean por defecto; use explícitamente `--allow-private`
para su propio endpoint de pruebas. `--timeout` vale 20 segundos por defecto y admite valores
mayores que 0 y menores o iguales que 300.

La captura nunca sustituye una instantánea existente. El código de salida `0` indica que se guardó
una observación completa; `1`, que se guardó una observación fallida; `2`, una entrada inválida o un
fallo de escritura. Una observación fallida no representa una lista vacía obtenida correctamente.
La instantánea capturada es evidencia local sin firma: los hashes del informe permiten detectar
cambios en las entradas, pero no autentican a su creador.

## Conservar la procedencia de las observaciones

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

Una observación correcta necesita el array completo `tools`. Conserve la fecha real con zona
horaria, el protocolo observado y el contexto de arranque. Distinga la versión observada del
paquete de la `serverVersion` declarada por el propio servidor. En un endpoint remoto,
`packageVersion` puede ser `null`. El informe muestra un hash de `launch` y el transporte conocido,
sin copiar comandos, argumentos ni valores del entorno; las instantáneas originales aún pueden
contener datos sensibles.

Al importar datos de HISTOR, asocie las definiciones con la observación histórica exacta. La
implementación local de la API expone `timeline[].protocolVersion`; cópielo a `protocol` cuando
esté presente. Una API anterior o desplegada puede omitirlo: conserve esa ausencia. Una los
`toolExtras` históricos a las herramientas por nombre solo si pertenecen a esa observación.
Las definiciones y extras actuales no pueden completar una instantánea antigua. El prototipo
no incluye un importador automático de la API.

Para una fuente MTL/1 que solo contenga `name`, `description`, `inputSchema` y `outputSchema`,
establezca `extrasCoverage` en `"unknown"`. El informe compara esos campos básicos, omite la
comparación de extras y deja la comparabilidad general como desconocida. `"observed"` significa
que la instantánea contiene todos los extras anunciados en esa observación; también es el valor
por defecto para las entradas locales v1 existentes.

## Leer los resultados y elegir las pruebas

| Estado de la dependencia | Significado y siguiente paso |
|---|---|
| `changed` | Cambiaron definiciones observadas comparables. Revise las herramientas seleccionadas afectadas y sus flujos. |
| `unchanged` | No cambiaron las definiciones observadas comparables. El comportamiento en ejecución no se ha probado. |
| `context_changed` | Cambiaron condiciones conocidas del protocolo o del arranque. Reproduzca condiciones comparables antes de atribuir diferencias a una actualización del paquete. |
| `unknown` | Falta una pareja de observaciones correctas o contexto de comparación, las fechas no están en orden, una herramienta seleccionada no aparece en ninguno de los lados o se desconoce la cobertura de extras. Resuelva las carencias indicadas. |

Los estados de flujo son `retest`, `no_observed_change` y `unknown`. Una dependencia puede cambiar
fuera de sus herramientas seleccionadas; use el estado de cada flujo para decidir el alcance de
las pruebas. `fieldCoverage` vale `full`, `core-only` o `none`. Las diferencias conocidas siguen
visibles aunque la comparabilidad general sea desconocida. Los hints de annotations siguen
siendo declaraciones del servidor.

`serializedUtf8Bytes` mide el JSON compacto presentado al modelo en bytes UTF-8. No mide tokens,
una factura ni ahorro conseguido. Los valores SHA-256 identifican los bytes exactos de entrada.
Ni las definiciones sin cambios ni la generación correcta de un informe establecen la corrección
o la seguridad del comportamiento en ejecución.

## Límites y resolución de problemas

El prototipo admite hasta 30 dependencias, 128 herramientas seleccionadas por dependencia,
64 flujos por dependencia y 2000 herramientas por instantánea. La lista de seguimiento tiene un
límite de 256 000 bytes y cada instantánea de 2 000 000 bytes. Se rechazan claves JSON duplicadas,
campos de formato desconocidos, números no finitos, definiciones de herramientas inválidas y
estructuras que superen 48 niveles o 100 000 nodos.

El comando de informe termina con `0` al escribir los informes, incluidos los que tienen resultados
`unknown`; `2` indica una entrada inválida o un fallo de escritura. Puede reutilizar un directorio
de salida existente: se sustituyen `report.md` y `report.json`, pero se rechaza sobrescribir una
entrada. Si aparece `No module named histor`, ejecute desde el directorio `histor/` de la copia
del código. Para un resultado `unknown`, consulte `limitations`, `sources` y `fieldCoverage`
antes de decidir sobre la actualización.
