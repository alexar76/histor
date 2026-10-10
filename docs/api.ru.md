# API

> 🌐 [English](api.md) · **Русский** · [Español](api.es.md) · [Français](api.fr.md) · [中文](api.zh.md)

Базовый URL: `https://histor.modelmarket.dev`. Всё публично, без аутентификации и только для
чтения, кроме `POST /api/v1/check` (с ограничением частоты) и маршрута оператора. Публичные чтения
отдают `Access-Control-Allow-Origin: *`. OpenAPI: [`/api/openapi.json`](https://histor.modelmarket.dev/api/openapi.json).

```mermaid
flowchart LR
    subgraph Read["чтение (GET, публично)"]
        S["/api/v1/servers<br/>/api/v1/servers/&lt;id&gt;"]
        C["/api/v1/changes · /feed.xml"]
        L["/api/v1/labels/&lt;id&gt;<br/>/api/v1/labels/&lt;id&gt;/proof"]
        E["/api/v1/toolsets/&lt;sri&gt;<br/>/api/v1/descriptors/&lt;sri&gt;<br/>/api/v1/pattern-sets/&lt;sri&gt;<br/>/api/v1/record-sets/&lt;sri&gt;"]
        G["/api/v1/log/sth · /log/entries<br/>/log/proof/inclusion · /log/proof/consistency"]
        ST["/api/v1/stats · /stats/hosts<br/>/api/v1/badges/&lt;name&gt; · /badge/&lt;id&gt;.svg"]
    end
    subgraph Write["запись"]
        K["POST /api/v1/check<br/>с ограничением частоты"]
        A["POST /api/v1/admin/crawl<br/>x-histor-operator"]
        F["POST /ai-market/v2/invoke<br/>федерация Hub"]
    end
```

## Серверы

| Метод | Путь | Возвращает |
|---|---|---|
| GET | `/api/v1/servers?q=&state=&limit=&offset=` | эндпоинты; `state` ∈ `all`, `pinned`, `changed`, `flagged`, `unobserved`; `q` ищет по имени, эндпоинту и заголовку |
| GET | `/api/v1/servers/<id>` | один эндпоинт: текущий набор инструментов, совпадения WARDEN с позициями в тексте, метки, свёрнутая хронология, изменения, сообщения клиентов за 30 дней |
| GET | `/badge/<id>.svg` | бейдж для README: `pinned ‹date›`, `unchanged Nd`, `changed ‹date›`, `not observed`, `not listed` |

`<id>` — первые 16 шестнадцатеричных символов `sha256(name + "\n" + endpoint)`: стабильный, по
одному на удалённый эндпоинт.

## Изменения

| Метод | Путь | Возвращает |
|---|---|---|
| GET | `/api/v1/changes?limit=&before=` | сначала новые; у каждого `summary.added`, `summary.removed`, `summary.modified[].fields` (пословный дифф описаний, дифф схем по JSON-путям) |
| GET | `/api/v1/changes/<id>` | одно изменение |
| GET | `/feed.xml` | Atom-лента последних 50 изменений; `?watch=<id>,<id>,…` (до 100 идентификаторов целей, как их дают `/check` и страницы серверов) оставляет только эти серверы и пакеты — подписка в любом читателе лент |

## Метки и свидетельства

| Метод | Путь | Возвращает |
|---|---|---|
| GET | `/api/v1/labels/<id>` | подписанная метка, `application/vc`, неизменяемая |
| GET | `/api/v1/labels/<id>/proof?tree_size=` | индекс листа, подписанная вершина дерева (STH) и доказательство включения |
| GET | `/api/v1/toolsets/<sri>` · `/descriptors/<sri>` · `/pattern-sets/<sri>` · `/record-sets/<sri>` | свидетельства с адресацией по содержимому, неизменяемые |
| GET | `/api/v1/issuer` | `did:key` журнала, сырой открытый ключ, текущие дайджесты набора шаблонов и набора записей, пакет WARDEN |

## Журнал

| Метод | Путь | Возвращает |
|---|---|---|
| GET | `/api/v1/log/sth` | последняя подписанная вершина дерева |
| GET | `/api/v1/log/sth/<size>` | вершина, подписанная при этом размере |
| GET | `/api/v1/log/entries?start=&end=` | листья по порядку (≤ 1000 за вызов) |
| GET | `/api/v1/log/proof/inclusion?leaf_index=&tree_size=` | доказательство включения по RFC 9162, hex |
| GET | `/api/v1/log/proof/consistency?first=&second=` | доказательство согласованности по RFC 9162, hex |

## Журнал квитанций

Второй, независимый журнал: якоря рабочих квитанций рынка (AWR/2), которые присылают выпустившие
их хабы. Якорь несёт четыре факта — digest квитанции, `did:key` издателя, время выпуска и подпись
издателя над ними — и никогда саму квитанцию. Его вершины — `histor.receipts-sth/v1`, подписаны
тем же ключом, что и у журнала меток, но над отдельным деревом.

| Метод | Путь | Возвращает |
|---|---|---|
| POST | `/api/v1/receipts/anchors` | `{"anchors": [...]}` (1–100) → по результату на якорь: `logged`, `duplicate` или `refused` с причиной |
| GET | `/api/v1/receipts/sth` | последняя подписанная вершина журнала квитанций |
| GET | `/api/v1/receipts/sth/<size>` | вершина, подписанная при этом размере |
| GET | `/api/v1/receipts/proof?digest=&tree_size=` | якорь, индекс листа и доказательство включения по RFC 9162 относительно подписанной вершины |
| GET | `/api/v1/receipts/proof/consistency?first=&second=` | доказательство согласованности по RFC 9162, hex |

Приём открыт только издателям из `HISTOR_RECEIPT_ISSUERS` (`did:key` через запятую; пусто —
журнал закрыт, так по умолчанию) и только по собственной подписи издателя Ed25519 над байтами
RFC 8785 полей `type`, `issuer`, `receiptDigest` и `issuedAt`. `issuedAt` может отставать до
30 дней (накопившееся после сбоя) и опережать не больше чем на 5 минут.

## /check

```http
POST /api/v1/check
Content-Type: application/json

{
  "endpoint": "https://example.com/mcp",      // or "name": "io.github.org/server"
  // a stdio server instead: "package": "npm:@scope/name" or "pypi:name"
  "tools": [ … ],                             // the tools array you received, or:
  "toolSetDigest": "sha256-…",                // its MTL/1 digest
  "contribute": true                          // optional: add (endpoint, digest, day) to a count
}
```

```json
{
  "type": "histor.check/v1",
  "checkedAt": "2026-09-23T10:02:11Z",
  "issuer": "did:key:z6Mk…",
  "query": { "endpoint": "…", "toolSetDigest": "sha256-…" },
  "target": { "id": "9c7f2af79fa057b2", "page": "…/s/9c7f2af79fa057b2", "badge": "…", "lastStatus": "ok" },
  "observed": { "toolSetDigest": "sha256-…", "unchangedSince": "…", "observations": 12, "changes": 0 },
  "match": "same",
  "note": "The tool definitions you sent are the set HISTOR currently observes at this endpoint.",
  "patternScan": { "status": "scanned", "block": 0, "advise": 1, "matches": [ … ] },
  "log": { "treeSize": 21480, "rootHash": "…", "timestamp": "…" },
  "signature": { "alg": "Ed25519", "verificationMethod": "…", "value": "…" }
}
```

`match` принимает одно из значений `same`, `different`, `previously-observed` (с `seenBefore`),
`not-observed`, `not-listed`, `no-digest`. Ответ подписан так же, как вершина дерева (`type`
`histor.check/v1`). Если набор ещё ни разу не сканировался, а у вашего адреса остался бюджет
сканирований, сайдкар WARDEN сканирует его на месте. Лимиты: 30 проверок в минуту и 20 новых
сканирований в час на адрес; тела запросов больше 2 MiB отклоняются. С `contribute` не хранится
ничего, кроме дневного счётчика для этого дайджеста.

Для stdio-сервера (пакета npm или PyPI, который запускает ваш клиент) передайте `package` — `npm:<имя>` или `pypi:<имя>` — вместо `endpoint`; имена PyPI нормализуются за вас. `target.packageVersion` — версия, которую HISTOR последней прочитал в своей [песочнице для пакетов](operations.ru.md#песочница-для-пакетов). Имя пакета никогда не считается незнакомым хостом. Ещё два ответа для пакета. `packageLookalike` (`of`, `weekly`, `how`, `ownWeekly`) — для любого пакета, о котором спросили, в списке он или нет, — называет популярный пакет, на который это имя похоже: на один символ, то же имя под другим scope или с другими разделителями, если популярный скачивают хотя бы в 100 раз чаще; общие имена вроде `mcp-server` ничьи. `target.packageSignals` — что реестр говорит о прочитанной HISTOR версии: `provenance` (подтверждённая сборка в CI), `publisher` (никогда не email), `installScripts` и `flags` — что изменилось с предыдущей прочитанной версии: `provenance-lost`, `publisher-changed`, `install-scripts-added`, `new-dependencies`. Это признаки украденного токена публикации.

## Статистика и живые бейджи

| Путь | Возвращает |
|---|---|
| `/api/v1/stats` | последний обход (размер реестра, что ответили эндпоинты, к чему не подключались, сколько меток выпущено), текущие счётчики, изменения за последние 24 ч / 7 дн / 30 дн, размер журнала, сообщения клиентов за 7 дн |
| `/api/v1/stats/hosts?limit=` | число эндпоинтов на хост среди тех, к которым подключались |
| `/api/v1/badges/log` · `pinned` · `changes` · `sth` | JSON для [эндпоинт-бейджа shields.io](https://shields.io/badges/endpoint-badge) в README |
| `/health` | liveness, бэкенд хранилища, размер дерева, идёт ли обход, последняя ошибка обхода |

## Федерация (AIMarket Hub)

HISTOR — пир AIMarket: `/.well-known/ai-market.json` (подписан целиком), `/ai-market/v2/manifest`
(подписан по канонической форме манифеста Hub) и `POST /ai-market/v2/invoke` с тремя бесплатными
capability:

| Capability | Вход | Результат |
|---|---|---|
| `histor.check@v1` | тело `/check` | подписанный ответ `/check` |
| `histor.server@v1` | `{"endpoint": …}` или `{"name": …}` | до 10 подходящих эндпоинтов |
| `histor.changes@v1` | `{"limit": 1–100}` | самые новые изменения |

Каждый ответ несёт interop-квитанцию, которую Hub верифицирует по ключу из `.well-known`; при
`HISTOR_PQC=1` это гибридная подпись Ed25519 + ML-DSA-65.

## Оператор

`POST /api/v1/admin/crawl` с `x-histor-operator: $HISTOR_OPERATOR_TOKEN` сразу запускает обход
(202) или отвечает 409, если обход уже идёт. Без настроенного токена маршрут отвечает 503.

`GET /api/v1/admin/unlisted?days=7&limit=100` (тот же заголовок) показывает хосты, о которых спрашивали `/check`, но которых HISTOR не знает, — самые частые первыми: `host`, `queries`, `days`, `first_day`, `last_day`. Считается только имя хоста https-эндпоинта с публичным DNS-именем — никогда путь или параметры, адрес спрашивающего или инструменты — не чаще раза в день на адрес и хост; дни старше 90 удаляются. Так собственный список HISTOR (`histor/curated.json`) пополняется серверами, которыми люди действительно пользуются.
