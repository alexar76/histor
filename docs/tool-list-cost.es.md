# Cuánto cuesta una lista de herramientas

> 🌐 [English](tool-list-cost.md) · [Русский](tool-list-cost.ru.md) · **Español** · [Français](tool-list-cost.fr.md) · [中文](tool-list-cost.zh.md)

Cada servidor MCP al que se conecta un cliente enumera sus herramientas: para cada una, un nombre, una descripción
y un esquema de entrada. El cliente pone esa lista en el prompt de cada petición al modelo, pregunte lo que pregunte
el usuario. Un lector de nuestro estudio de 12 915 paquetes (Paul Irolla) propuso contarlo en tokens. HISTOR guarda
la lista de herramientas actual de cada servidor que observa, así que lo contamos.

## Las cifras

Instantánea del 2026-10-19: cada paquete (npm, PyPI) y cada servidor remoto del registro con al menos una
herramienta, con su lista actual. Una lista se cuenta tal como un cliente la envía a un modelo: para cada
herramienta, `{name, description, input_schema}` en JSON compacto. El tokenizador es `o200k_base` (tiktoken 0.12,
la familia GPT-4o).

| | Paquetes | Servidores remotos |
|---|---|---|
| Servidores | 18 204 | 16 170 |
| Definiciones de herramientas | 337 449 | 232 427 |
| Herramientas por servidor: mediana / media / percentil 90 | 8 / 18,5 / 40 | 7 / 14,4 / 31 |
| **Tokens por servidor: mediana** | **1 272** | **1 328** |
| media | 3 812 | 3 412 |
| percentil 90 | 8 361 | 7 425 |
| percentil 99 | 39 612 | 22 294 |
| el mayor | 304 780 | 547 935 |
| Tokens por herramienta: mediana / media | 129 / 206 | 231 / 237 |
| Servidores con más de 10 000 tokens | 1 447 | 618 |
| Servidores con más de 100 000 tokens | 31 | 9 |

La media triplica la mediana: unos pocos servidores con cientos o miles de herramientas concentran la mayoría de los
tokens. Las «unas 24 herramientas por servidor» del estudio eran la media de los 7 656 paquetes que respondieron el
2026-10-13; en el conjunto actual, mayor, la media es 18,5 y la mediana 8.

## En dinero

La lista se reenvía con cada petición, así que su coste es tokens × precio × peticiones. Por cada 1 000 peticiones,
a precios de lista del 2026-10-19 (OpenRouter):

| La lista de un servidor | Tokens | 0,10 USD por millón (Claude Haiku 5.5) | 2 USD por millón (Claude Sonnet 5.5) | Sonnet 5.5, leída de caché (0,10 USD por millón) |
|---|---|---|---|---|
| paquete mediano | 1 272 | 0,13 USD | 2,54 USD | 0,13 USD |
| paquete medio | 3 812 | 0,38 USD | 7,62 USD | 0,38 USD |
| percentil 90 | 8 361 | 0,84 USD | 16,72 USD | 0,84 USD |
| percentil 99 | 39 612 | 3,96 USD | 79,22 USD | 3,96 USD |

Un cliente con cinco servidores medianos envía unos 6 400 tokens de definiciones de herramientas antes de la primera
palabra del usuario. Las listas más grandes ni siquiera caben: 304 780 tokens superan una ventana de contexto de
200 000 tokens.

Dos cosas reducen la factura. La caché de prompt, cuando el cliente mantiene la lista de herramientas idéntica y al
principio del prompt: con el precio de lista de Sonnet 5.5, una lectura de caché cuesta la vigésima parte. Y cargar
las definiciones bajo demanda: el modelo ve primero los nombres de las herramientas y recibe un esquema cuando va a
llamar a esa herramienta.

## Qué no mide esto

- **Un solo tokenizador.** Cada modelo tokeniza a su manera, así que en otros modelos las cifras varían, normalmente
  en decenas de por ciento; lo que se mantiene es la forma: una mediana cercana a 1 300 tokens y una cola larga.
- **El texto propio de los proveedores.** La API de un modelo añade sus propias instrucciones de uso de herramientas
  alrededor de las definiciones; no se cuentan.
- **Lo que los clientes envían de verdad.** Algunos quitan campos o acortan las descripciones largas; otros envían
  también el esquema de salida. Contadas completas, como se listan, la lista de un paquete tiene una mediana de 1 390
  tokens y un percentil 90 de 9 960.
- **Qué servidores usa la gente.** Cada servidor listado cuenta una vez: uno popular y uno abandonado pesan lo mismo.

## Reproducir

[`scripts/tool_list_tokens.py`](../scripts/tool_list_tokens.py) lee una exportación de las listas de herramientas
actuales (la consulta está en su docstring) y escribe el resumen. El nuestro, sin nombres de servidores:
[`tool-list-tokens-2026-10-19.json`](tool-list-tokens-2026-10-19.json).
