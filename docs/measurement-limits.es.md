# Cómo leer las mediciones de HISTOR

> 🌐 [English](measurement-limits.md) · [Русский](measurement-limits.ru.md) · **Español** · [Français](measurement-limits.fr.md) · [中文](measurement-limits.zh.md)

Aclaración por preguntas de lectores, 2026-10-25. Son límites de observaciones y comparaciones, no garantías sobre cada instalación o servidor vivo. Código local, servicio desplegado y distribución Python publicada son estados distintos.

## Fallar al instalar no establece la causa

`sdk-incompatible` es una heurística sobre el texto de salida. El clasificador antiguo incluía extras ausentes y errores MCP 1/Pydantic cuando el traceback mencionaba FastMCP; no probaba que una dependencia sin límite instalara MCP 2. La repetición diagnóstica con `mcp<2` cambia el entorno y no sustituye la observación principal de instalación nueva ni sus tools.

La cohorte reconstruida de 292 paquetes se reprobó en Linux/gVisor sin nuestro `mcp<2` forzado: 12 versiones nuevas enumeraron tools, otras dos versiones sin cambios funcionaron con su extra MCP declarado, 276 seguían fallando y dos necesitaban autenticación real o un índice de proyecto preparado. Once recuperaciones tienen un control nuevo de la versión antigua que falla; una versión antigua ya no estaba disponible. No demuestra 292 fallos por MCP 2, seguridad ni funcionalidad autenticada completa. [Resultados y controles](https://dev.to/alexar76/we-rechecked-292-failing-mcp-packages-12-newer-releases-now-list-tools-p72).

## Tokens y CI

Compruebe por separado total, descripciones y esquemas de entrada: una reducción puede ocultar otro aumento. Mantenga un baseline revisado, sin regenerarlo justo antes de cada check. El [ejemplo público copiable](https://dev.to/alexar76/a-copyable-mcp-tool-cost-check-for-ci-with-passing-and-failing-examples-3hl5) fija CLI publicado 0.3.1 y tokenizer 0.14.0, verifica snapshots guardados y no llama tools de textokit. El cambio real es 5.565 → 3.776 tokens; invertir la comparación devuelve **1** intencionadamente, no demuestra una regresión posterior. La comparación normal devuelve **0** y entrada inválida **2**.

Se cuenta una representación compacta concreta mediante `o200k_base`. Wrappers, subconjuntos de tools, carga bajo demanda, tokenizer y lecturas/escrituras de caché cambian el uso real. Las tablas históricas son escenarios a su fecha, no facturas ni precios actuales; la factura exige los registros de uso del cliente. [Guía de costes](tool-list-cost.es.md).

## Una edición numérica no es necesariamente cosmética

Las vistas de espacios o números enmascarados ayudan a revisar; no sustituyen el digest MTL canónico literal ni continuity. $0.002 → $0.02 es numérico y potencialmente importante: [cambio 13185](https://histor.modelmarket.dev/api/v1/changes/13185). Separe servidores de transiciones, descripciones de esquemas/composición, indicadores solapados de categorías exclusivas y estados raw ausentes de estados sin cambios. [Etiquetas y digests](labels.es.md).

## Un pago no identifica a un cliente

`histor payer-audit`, disponible en el código fuente, separa liquidación, señales de comportamiento y anotaciones de fuentes vinculadas a transacciones. La consulta PyPI del 2026-10-25 devolvió **0.3.1**; esa distribución no contenía el comando. Una versión escrita en el checkout no prueba publicación. [Ejecutar desde el código, EN](payer-audit.md).

Una transferencia Base USDC coincidente no prueba entrega, propiedad del monedero ni intención. La marca own-wallet del vendedor y una afirmación sobre inputExample siguen siendo declaraciones atribuidas hasta comprobarse independientemente. `unknown` no confirma un cliente orgánico; `probe-like` es un indicio no calibrado, no fraude ni probabilidad. Un pago de investigación no clasifica para siempre un monedero. Todos los pagos, incluidos probes y monitorización, permanecen en los totales monetarios. [Ejemplo de cinco pagadores](https://dev.to/alexar76/five-wallets-paid-an-x402-seller-what-can-we-say-about-demand-5gek).

## El sondeo pierde estados intermedios

Remote HTTP usa una cola independiente: seis horas normalmente, objetivo de 30 minutos tras al menos dos cambios observados en siete días. Descubrimiento del registro y planificación de paquetes conservan su calendario diario. Son intervalos objetivo, no promesas de latencia: caídas, retrasos, rate limits y backoff alargan los huecos. Revise `remotePolling` en `/api/v1/stats` y los intervalos reales. A→B→A entre consultas es invisible; más frecuencia reduce el hueco sin registrar cada transición. [Operación detallada, EN](remote-polling.md).

## Límites del canary y del launcher

Un `initialize`/`tools/list` correcto o un canary sintético no prueba todas las entradas ni gratuidad. El canary puede elegir preview gratuito y los argumentos omitidos activar un default de pago. `readOnlyHint` es declaración del servidor, no certificación de facturación; error o timeout no prueba ausencia de efectos. Lecturas remotas y canaries de paquetes son mediciones distintas.

Para comparar launchers guarde dos snapshots completos, comando exacto, versión runtime/runner y condiciones. La versión del paquete, el runner real y `serverInfo.version` autodeclarado pueden diferir. Leer historia no es una nueva ejecución ni un estudio de usabilidad humana; anotaciones y versiones necesitan procedencia propia.
