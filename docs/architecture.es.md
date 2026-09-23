# Arquitectura

> 🌐 [English](architecture.md) · [Русский](architecture.ru.md) · **Español** · [Français](architecture.fr.md) · [中文](architecture.zh.md)

HISTOR es un único servicio Python con un proceso auxiliar. Todo lo que sabe procede de dos
lecturas —el registro oficial de MCP y el `tools/list` de cada servidor listado— y todo lo que
publica lo firma una sola clave y se añade a un único registro de transparencia.

```mermaid
flowchart TB
    subgraph Outside["Exterior, no confiable"]
        REG["registry.modelcontextprotocol.io<br/>/v0/servers?version=latest"]
        MCP["~20k endpoints MCP remotos<br/>streamable-http"]
        CLIENT["clientes, registros de MCP,<br/>autores de servidores"]
    end
    subgraph Service["Servicio HISTOR (python -m histor serve)"]
        SCHED["planificador<br/>cada HISTOR_CRAWL_INTERVAL_S"]
        CRAWL["rastreador<br/>recolectar · observar · confirmar"]
        GUARD["netguard<br/>resuelve una vez, rechaza lo privado,<br/>fija la dirección comprobada"]
        READER["cliente MCP de solo lectura<br/>initialize · tools/list"]
        SUBJ["sujeto MTL/1 + resúmenes"]
        LABELS["emisor de etiquetas<br/>AWR/2 · did:key"]
        LOG["logbook<br/>hojas RFC 9162 + STH"]
        API["FastAPI<br/>panel · API · insignia · feed · /check"]
    end
    SIDE["sidecar de WARDEN<br/>node scanner/scan.mjs<br/>@aimarket/warden@0.5.0"]
    DB[("Postgres (prod)<br/>SQLite (dev)")]
    KEY[["/data/issuer.key<br/>/data/provider.key"]]

    SCHED --> CRAWL
    CRAWL --> REG
    CRAWL --> READER --> GUARD --> MCP
    CRAWL --> SUBJ --> LABELS --> LOG --> DB
    CRAWL -- "conjuntos de herramientas nuevos, por lotes" --> SIDE
    LABELS --- KEY
    API --> DB
    CLIENT --> API
```

## Módulos

| Módulo | Responsabilidad |
|---|---|
| `histor/registry.py` | Recorre las páginas del registro oficial de MCP (`version=latest`, con reintentos: cada página tarda de 1 a 50 s), conserva una entrada por nombre y convierte cada remoto en un destino. Los endpoints que no contactará se conservan con un motivo (`transport-not-observed`, `templated-url`, `cleartext-url`), para que las cifras públicas nunca reduzcan su denominador. |
| `histor/netguard.py` | Resuelve el host una sola vez, rechaza el destino si **cualquiera** de las respuestas no es pública y después se conecta a la dirección comprobada, con `Host` y el SNI de TLS fijados al nombre. El DNS rebinding nunca obtiene una segunda consulta. |
| `histor/mcpclient.py` | `initialize` → `notifications/initialized` → `tools/list` recorrido por completo mediante `nextCursor`. Streaming con límite de tamaño, SSE leído hasta nuestro id de respuesta, sin redirecciones, miembros JSON duplicados rechazados. No tiene ninguna ruta de código que llame a una herramienta. |
| `histor/subject.py` | El descriptor MTL/1 y ambos resúmenes criptográficos (digests), sobre el canonicalizador de referencia de AWR/2. Una prueba de paridad byte a byte lo contrasta con el propio `mtl_subject.py` del perfil. |
| `histor/scanner.py` + `scanner/scan.mjs` | Ejecuta las puertas **publicadas** de WARDEN. Al arrancar, el sidecar describe su propio conjunto de patrones y su conjunto de registros, de modo que una etiqueta nombra el resumen de las reglas que realmente se ejecutaron. |
| `histor/labels.py` | Cuatro métodos de etiqueta, cada uno un documento AWR/2 firmado. Las reglas de veredicto de MTL/1 se imponen en el código (`MTL-METH-002`). |
| `histor/logbook.py` + `histor/merkle.py` | Adición de hojas, encabezados de árbol, pruebas de inclusión y de consistencia. Solo se almacenan subárboles completos, así que cada prueba cuesta O(log n) lecturas. |
| `histor/crawler.py` | Un rastreo: recolectar → observar (pool de hilos, límite por host, hosts intercalados) → escanear los conjuntos de herramientas nuevos → una transacción por destino, en lotes de `HISTOR_CRAWL_BATCH` guardados antes de leer el siguiente → firmar un encabezado de árbol. |
| `histor/check.py` | `/check`: compara el conjunto de herramientas de un cliente con lo observado; recuento de resúmenes opcional y voluntario. |
| `histor/app.py` | La interfaz HTTP: panel, API, insignias, feed Atom, ruta del operador y el peer de federación de AIMarket. |
| `histor/db.py` + `histor/migrations.py` + `histor/store.py` | Almacenamiento independiente del backend: un solo dialecto SQL para SQLite y Postgres, migraciones numeradas y un contrato de columnas que se comprueba al arrancar. |

## Un rastreo

```mermaid
sequenceDiagram
    autonumber
    participant S as scheduler
    participant C as crawler
    participant R as registry
    participant M as MCP endpoints
    participant W as WARDEN sidecar
    participant DB as store
    S->>C: intervalo cumplido (o POST /api/v1/admin/crawl del operador)
    C->>R: GET /v0/servers?version=latest (paginado, con reintentos)
    R-->>C: ~21k servidores, ~22k remotos
    C->>DB: upsert de destinos, marca los retirados (solo tras una recolección completa)
    C->>W: describe → conjunto de patrones + conjunto de registros, con su resumen
    loop lotes de 400 endpoints, cada uno guardado antes de leer el siguiente
    par 12 hilos, ≤ 2 por host, hosts intercalados
        C->>M: initialize, tools/list (todas las páginas)
        M-->>C: conjunto de herramientas o un estado (http-401, timeout, …)
    end
    C->>W: escanea los conjuntos nunca vistos (lotes de 64)
    loop una transacción por destino
        C->>DB: fila de observación
        C->>DB: sujeto nuevo → etiquetas de observación + escaneo + nombre, añadidas
        C->>DB: etiqueta previa → etiqueta de continuidad (pass / fail / inconclusive)
        C->>DB: fail → fila de cambio con un diff por herramienta
    end
    end
    C->>DB: firma el encabezado de árbol sobre el nuevo tamaño
```

## Cuándo se emite una etiqueta

El registro debe recoger hechos, no ruido, así que los resultados deterministas idénticos no se
vuelven a emitir cada día.

```mermaid
stateDiagram-v2
    [*] --> Unseen
    Unseen --> Pinned: tools/list ok<br/>etiquetas de observación + escaneo + nombre
    Unseen --> Undigestible: MTL-SUBJ-001/002/003<br/>una observación inconclusive
    Pinned --> Pinned: mismo resumen, ≥ 20 h desde el último eslabón<br/>continuidad pass (unchangedSince)
    Pinned --> Changed: resumen distinto<br/>continuidad fail + nuevas etiquetas de observación/escaneo + diff
    Changed --> Pinned: siguiente rastreo, mismo resumen<br/>continuidad pass
    Pinned --> Unreachable: estado ≠ ok<br/>UNA continuidad inconclusive
    Unreachable --> Unreachable: sigue fallando<br/>no se emite nada
    Unreachable --> Pinned: vuelve con el mismo resumen<br/>continuidad pass
    Unreachable --> Changed: vuelve con un resumen nuevo<br/>continuidad fail
```

Un nuevo conjunto de patrones o de registros de WARDEN (un nuevo paquete publicado) vuelve a emitir
las etiquetas de escaneo de todos los sujetos actuales, porque dos etiquetas de escaneo solo son
comparables bajo el mismo conjunto.

## Modelo de datos

```mermaid
erDiagram
    targets ||--o{ observations : "una por intento"
    targets ||--o{ labels : "emitidas sobre"
    targets ||--o{ changes : "fail de continuidad"
    targets ||--o{ client_reports : "recuentos voluntarios de resúmenes"
    labels ||--|| log_nodes : "hoja = nivel 0"
    blobs ||--o{ labels : "evidencia por resumen"
    sths }o--|| log_nodes : "fija la raíz"
    targets {
        text id PK "sha256(name, endpoint)[:16]"
        text name
        text endpoint
        text current_subject "resumen del sujeto (SRI)"
        text current_toolset "resumen del conjunto de herramientas (SRI)"
        text unchanged_since
        text chain_label "último eslabón para la continuidad"
    }
    labels {
        text id PK "urn:uuid"
        text method
        text verdict
        text digest UK "SRI de JCS(etiqueta)"
        bigint leaf_index UK
        text body "el documento firmado"
    }
    log_nodes {
        int level PK
        bigint idx PK
        text hash "solo subárboles completos"
    }
    blobs {
        text digest PK
        text kind "toolset · descriptor · pattern-set · record-set"
        text body
    }
```

## Almacenamiento y migraciones

SQLite es el almacén de desarrollo: un archivo bajo `HISTOR_DATA_DIR`, modo WAL y una conexión por
lectura, para que ningún lector se quede anclado en una instantánea antigua bloqueando los
checkpoints. Postgres es el de producción: `HISTOR_PROFILE=prod` se niega a arrancar sin una URL
`postgresql://`. Todo el SQL se escribe una sola vez en el subconjunto que comparten ambos motores;
donde discrepan (columnas de identidad), una migración lleva una sentencia por backend. Las
adiciones al registro toman un bloqueo consultivo (advisory lock) de Postgres dentro de su
transacción, de modo que dos procesos apuntados a una misma base de datos siguen añadiendo hojas de
una en una.

Las migraciones siguen el estilo de la casa, compartido con HESTIA y el Hub: una tabla
`schema_migrations`, una lista numerada de revisiones, cada revisión en su propia transacción y
fallos ruidosos (fail-loud). Una revisión ya publicada nunca se edita; se rechaza una base de datos
que haya aplicado una revisión que esta compilación no conoce.

## Claves

| Archivo | Firma | Por qué por separado |
|---|---|---|
| `issuer.key` | cada etiqueta (AWR/2, `did:key`), cada encabezado de árbol firmado o STH (`histor.sth/v1`), cada respuesta de `/check` (`histor.check/v1`) | una sola identidad para todo lo que verifica un lector; el `type` dentro de los bytes firmados impide que una firma sobre un tipo de documento se reutilice como si fuera de otro |
| `provider.key` (+ `_mldsa`) | los documentos de federación de AIMarket (`.well-known`, manifiesto, recibos de interoperabilidad), híbridos Ed25519 + ML-DSA-65 cuando `HISTOR_PQC=1` | habla el protocolo del Hub; ninguna de las dos claves puede firmar por la otra |

Ambas residen en el volumen de datos, con modo 0600, nunca en variables de entorno ni en la base de
datos; los enlaces y los archivos corruptos se rechazan al arrancar.
