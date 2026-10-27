"""Built-in upgrade brief prose. Source observations and machine identifiers are never translated."""

from __future__ import annotations

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("ru", "en", "es", "fr", "zh")

# Every row follows SUPPORTED_LANGUAGES. Keep message IDs independent of any source language.
_ROWS: dict[str, tuple[str, str, str, str, str]] = {
    "default_title": (
        "Персональный разбор обновлений MCP", "Personal MCP upgrade brief",
        "Análisis personal de actualizaciones MCP", "Analyse personnelle des mises à jour MCP", "个人 MCP 更新分析",
    ),
    "intro": (
        "Разбор сохранённых определений и перепроверок ваших рабочих сценариев.",
        "Review saved definitions and the checks needed for your workflows.",
        "Análisis de definiciones guardadas y comprobaciones de sus flujos de trabajo.",
        "Analyse des définitions enregistrées et des vérifications de vos flux de travail.",
        "分析已保存的定义，以及工作流程需要进行的复测。",
    ),
    "summary_header": (
        "| Зависимость | Результат | Сценарии для проверки |",
        "| Dependency | Result | Workflows to check |",
        "| Dependencia | Resultado | Flujos que comprobar |",
        "| Dépendance | Résultat | Flux à vérifier |",
        "| 依赖 | 结果 | 需要检查的流程 |",
    ),
    "status_changed": ("Есть изменения", "Changes observed", "Cambios observados", "Changements observés", "观测到变化"),
    "status_unchanged": (
        "Изменений в наблюдавшихся определениях нет", "No observed definition changes",
        "Sin cambios en las definiciones observadas", "Aucun changement des définitions observées", "观测到的定义未变化",
    ),
    "status_unknown": ("Недостаточно данных", "Insufficient data", "Datos insuficientes", "Données insuffisantes", "数据不足"),
    "status_context_changed": (
        "Изменились условия наблюдения", "Observation conditions changed", "Cambiaron las condiciones de observación",
        "Conditions d’observation modifiées", "观测条件发生变化",
    ),
    "status_unknown_changes": (
        "Определения различаются; сопоставимость неизвестна", "Definitions differ; comparability is unknown",
        "Las definiciones difieren; se desconoce su comparabilidad",
        "Les définitions diffèrent ; leur comparabilité est inconnue", "定义存在差异；可比性未知",
    ),
    "workflow_unknown": (
        "Нужны сопоставимые наблюдения", "Comparable observations are needed", "Se necesitan observaciones comparables",
        "Des observations comparables sont nécessaires", "需要可比的观测",
    ),
    "workflow_retest": ("Нужна перепроверка", "Retest needed", "Se requiere una nueva prueba", "Nouvelle vérification nécessaire", "需要复测"),
    "workflow_no_observed_change": (
        "Используемые определения без наблюдавшихся изменений", "No observed changes in the definitions used",
        "Sin cambios observados en las definiciones utilizadas", "Aucun changement observé dans les définitions utilisées",
        "使用的定义未观测到变化",
    ),
    "tool_changed": ("Изменён", "Changed", "Modificada", "Modifié", "已变更"),
    "tool_removed": ("Удалён", "Removed", "Eliminada", "Supprimé", "已移除"),
    "tool_added": ("Добавлен", "Added", "Añadida", "Ajouté", "已添加"),
    "tool_unchanged": (
        "Без наблюдавшихся изменений", "No observed changes", "Sin cambios observados", "Aucun changement observé", "未观测到变化",
    ),
    "before": ("До", "Before", "Antes", "Avant", "之前"),
    "after": ("После", "After", "Después", "Après", "之后"),
    "date_unknown": ("дата неизвестна", "date unknown", "fecha desconocida", "date inconnue", "时间未知"),
    "version_unknown": ("неизвестна", "unknown", "desconocida", "inconnue", "未知"),
    "protocol_unknown": ("неизвестен", "unknown", "desconocido", "inconnu", "未知"),
    "not_applicable": ("не применяется", "not applicable", "no corresponde", "sans objet", "不适用"),
    "source_summary": (
        "{side}: {date}; версия пакета {package}; версия сервера {server}; протокол {protocol}.",
        "{side}: {date}; package version {package}; server version {server}; protocol {protocol}.",
        "{side}: {date}; versión del paquete {package}; versión del servidor {server}; protocolo {protocol}.",
        "{side} : {date} ; version du paquet {package} ; version du serveur {server} ; protocole {protocol}.",
        "{side}：{date}；软件包版本 {package}；服务器版本 {server}；协议 {protocol}。",
    ),
    "coverage_core": (
        "Покрытие полей: только name, description, inputSchema и outputSchema. Полнота дополнительных полей неизвестна.",
        "Field coverage: name, description, inputSchema and outputSchema only. Completeness of extra fields is unknown.",
        "Cobertura: solo name, description, inputSchema y outputSchema. Se desconoce la exhaustividad de los campos adicionales.",
        "Champs couverts : name, description, inputSchema et outputSchema uniquement. L’exhaustivité des champs supplémentaires est inconnue.",
        "字段覆盖范围：仅 name、description、inputSchema 和 outputSchema。额外字段是否完整未知。",
    ),
    "coverage_none": (
        "Успешной пары определений нет. Поля инструментов не сравнивались.",
        "No successful pair of definitions is available. Tool fields were not compared.",
        "No hay una pareja de definiciones obtenidas correctamente. No se compararon los campos de herramientas.",
        "Aucune paire de définitions obtenues avec succès. Les champs des outils n’ont pas été comparés.",
        "没有成功获取的定义对；未比较工具字段。",
    ),
    "limitation": ("Ограничение", "Limitation", "Limitación", "Limite", "限制"),
    "metadata_missing": ("Нет метаданных", "Missing metadata", "Metadatos ausentes", "Métadonnées manquantes", "缺少元数据"),
    "context_changed": (
        "Другие условия: {fields}; разницу нельзя приписать только обновлению пакета.",
        "Changed conditions: {fields}; differences cannot be attributed only to a package update.",
        "Condiciones distintas: {fields}; las diferencias no se pueden atribuir solo a la actualización del paquete.",
        "Conditions modifiées : {fields} ; les différences ne peuvent pas être attribuées uniquement à la mise à jour du paquet.",
        "条件变化：{fields}；不能仅将差异归因于软件包升级。",
    ),
    "check_tool_missing": (
        "Получить полный успешный снимок: выбранный инструмент не найден ни с одной стороны.",
        "Obtain a complete successful snapshot: the selected tool is absent from both observations.",
        "Obtenga una instantánea completa y correcta: la herramienta seleccionada no aparece en ninguna observación.",
        "Obtenez un instantané complet et réussi : l’outil sélectionné est absent des deux observations.",
        "获取完整且成功的快照：两次观测均未找到所选工具。",
    ),
    "check_tool_added": (
        "Повторить рабочий сценарий с новым инструментом и проверить форму результата.",
        "Replay the workflow with the new tool and check the result structure.",
        "Repita el flujo con la nueva herramienta y compruebe la estructura del resultado.",
        "Rejouez le flux avec le nouvel outil et vérifiez la structure du résultat.",
        "使用新工具重放工作流程，并检查结果结构。",
    ),
    "check_tool_removed": (
        "Проверить замену или закрепить прежний пакет; не запускать сценарий вслепую.",
        "Check a replacement or pin the previous package; do not run the workflow without review.",
        "Compruebe una alternativa o fije el paquete anterior; revise el flujo antes de ejecutarlo.",
        "Vérifiez un remplacement ou épinglez le paquet précédent ; examinez le flux avant de l’exécuter.",
        "检查替代工具或固定旧版软件包；先审查流程，再执行。",
    ),
    "check_description": (
        "Прочитать изменённые инструкции; повторить обычный запрос и проверить выбор инструмента.",
        "Read the changed instructions; replay a typical request and check tool selection.",
        "Lea las instrucciones modificadas; repita una petición habitual y compruebe la selección de herramientas.",
        "Lisez les instructions modifiées ; rejouez une requête habituelle et vérifiez le choix de l’outil.",
        "阅读变更后的说明；重放常用请求并检查工具选择。",
    ),
    "check_input": (
        "Проверить сохранённые входы, обязательные поля и типы; добавить граничный пример.",
        "Check saved inputs, required fields and types; add a boundary case.",
        "Compruebe las entradas guardadas, los campos obligatorios y los tipos; añada un caso límite.",
        "Vérifiez les entrées enregistrées, les champs requis et les types ; ajoutez un cas limite.",
        "检查已保存的输入、必填字段和类型，并添加边界用例。",
    ),
    "check_output": (
        "Повторить обработку результата: проверить поля и типы, которые читает сценарий.",
        "Replay result handling: check the fields and types read by the workflow.",
        "Repita el procesamiento del resultado: compruebe los campos y tipos que lee el flujo.",
        "Rejouez le traitement du résultat : vérifiez les champs et types lus par le flux.",
        "重放结果处理过程，检查流程读取的字段和类型。",
    ),
    "check_extras": (
        "Проверить подтверждения действий, permissions и заявленные цены; annotations — заявления сервера.",
        "Check action confirmations, permissions and advertised prices; annotations are claims made by the server.",
        "Compruebe las confirmaciones, los permisos y los precios anunciados; las annotations son declaraciones del servidor.",
        "Vérifiez les confirmations, les permissions et les prix annoncés ; les annotations sont des déclarations du serveur.",
        "检查操作确认、权限和声明的价格；annotations 仅是服务器的声明。",
    ),
    "check_extras_missing": (
        "Получить связанные с обоими наблюдениями annotations и другие extras; текущие метаданные не заменяют исторические.",
        "Obtain annotations and other extras tied to both observations; current metadata cannot replace historical metadata.",
        "Obtenga las annotations y otros extras de ambas observaciones; los metadatos actuales no sustituyen a los históricos.",
        "Obtenez les annotations et autres extras des deux observations ; les métadonnées actuelles ne remplacent pas les métadonnées historiques.",
        "获取分别对应两次观测的 annotations 和其他 extras；当前元数据不能替代历史元数据。",
    ),
    "check_observations_missing": (
        "Получить оба полных успешных наблюдения в сопоставимом окружении.",
        "Obtain two complete successful observations in comparable environments.",
        "Obtenga dos observaciones completas y correctas en entornos comparables.",
        "Obtenez deux observations complètes et réussies dans des environnements comparables.",
        "在可比环境中取得两次完整且成功的观测。",
    ),
    "check_context": (
        "Сверить protocol, launcher и условия доступа; повторить оба снимка в одном контексте.",
        "Check protocol, launcher and access conditions; repeat both snapshots in the same context.",
        "Compruebe protocol, launcher y las condiciones de acceso; repita ambas instantáneas en el mismo contexto.",
        "Vérifiez protocol, launcher et les conditions d’accès ; reprenez les deux instantanés dans le même contexte.",
        "核对 protocol、launcher 和访问条件；在相同上下文中重新获取两份快照。",
    ),
    "control_run": (
        "Поведение не проверялось; обычный контрольный запуск остаётся полезным.",
        "Behavior was not tested; a normal control run is still useful.",
        "No se probó el comportamiento; sigue siendo útil una ejecución de control habitual.",
        "Le comportement n’a pas été testé ; une exécution de contrôle habituelle reste utile.",
        "尚未测试运行行为；常规对照运行仍然有用。",
    ),
    "selected_changes": (
        "Изменения выбранных инструментов", "Selected tool changes", "Cambios en las herramientas seleccionadas",
        "Changements des outils sélectionnés", "所选工具的变化",
    ),
    "required_added": (
        "Новые обязательные поля", "New required fields", "Nuevos campos obligatorios", "Nouveaux champs requis", "新增必填字段",
    ),
    "type_changes": ("Изменения type", "Type changes", "Cambios de type", "Changements de type", "type 变化"),
    "description_added": ("Описание добавлено", "Description added", "Descripción añadida", "Description ajoutée", "新增描述"),
    "description_removed": ("Описание удалено", "Description removed", "Descripción eliminada", "Description supprimée", "移除描述"),
    "truncated": ("сокращено", "truncated", "abreviado", "tronqué", "已截断"),
    "not_declared": ("не заявлено", "not declared", "no declarado", "non déclaré", "未声明"),
    "server_claim": (
        "Это заявление сервера.", "This is a server claim.", "Es una declaración del servidor.", "C’est une déclaration du serveur.", "这是服务器的声明。",
    ),
    "other_fields": (
        "Другие изменённые поля", "Other changed fields", "Otros campos modificados", "Autres champs modifiés", "其他变更字段",
    ),
    "extras_details": (
        "Изменились дополнительные поля определения; детали сохранены в JSON.",
        "Extra definition fields changed; details are saved in JSON.",
        "Cambiaron campos adicionales de la definición; los detalles están en JSON.",
        "Des champs supplémentaires de la définition ont changé ; les détails sont dans JSON.",
        "定义中的额外字段发生变化；详情已保存在 JSON 中。",
    ),
    "selected_unchanged": (
        "В выбранных определениях изменений не обнаружено.", "No changes were found in the selected definitions.",
        "No se detectaron cambios en las definiciones seleccionadas.", "Aucun changement détecté dans les définitions sélectionnées.",
        "所选定义中未发现变化。",
    ),
    "byte_counts": (
        "Размер model-facing JSON в UTF-8: весь список {before_full} → {after_full} байт; выбранные инструменты {before_selected} → {after_selected} байт.",
        "Model-facing JSON size in UTF-8: full list {before_full} → {after_full} bytes; selected tools {before_selected} → {after_selected} bytes.",
        "Tamaño del JSON presentado al modelo en UTF-8: lista completa {before_full} → {after_full} bytes; herramientas seleccionadas {before_selected} → {after_selected} bytes.",
        "Taille du JSON présenté au modèle en UTF-8 : liste complète {before_full} → {after_full} octets ; outils sélectionnés {before_selected} → {after_selected} octets.",
        "供模型读取的 JSON 的 UTF-8 大小：完整列表 {before_full} → {after_full} 字节；所选工具 {before_selected} → {after_selected} 字节。",
    ),
    "bytes_note": (
        "Это не токены и не фактическая экономия.", "These are not tokens or achieved savings.",
        "No son tokens ni ahorro conseguido.", "Il ne s’agit ni de tokens ni d’économies réalisées.", "这些数值不是 token 数，也不代表实际节省。",
    ),
    "limits_heading": ("Границы разбора", "Analysis limits", "Límites del análisis", "Limites de l’analyse", "分析范围限制"),
    "json_link": (
        "Полные данные, версии и SHA-256 входных файлов", "Full data, versions and input SHA-256 hashes",
        "Datos completos, versiones y hashes SHA-256 de las entradas", "Données complètes, versions et empreintes SHA-256 des entrées",
        "完整数据、版本和输入文件 SHA-256 哈希",
    ),
    "limit_definitions": (
        "Сравниваются наблюдавшиеся определения, не фактическое поведение сервера.",
        "Only observed definitions are compared, not the server’s runtime behavior.",
        "Solo se comparan las definiciones observadas, no el comportamiento del servidor en ejecución.",
        "Seules les définitions observées sont comparées, pas le comportement du serveur à l’exécution.",
        "仅比较观测到的定义，不验证服务器的运行时行为。",
    ),
    "limit_safety": (
        "Изменение не доказывает поломку; отсутствие изменений не подтверждает безопасность.",
        "A change does not prove breakage; unchanged definitions do not establish safety.",
        "Un cambio no demuestra un fallo; unas definiciones sin cambios no establecen la seguridad.",
        "Un changement ne prouve pas une panne ; des définitions inchangées ne démontrent pas la sécurité.",
        "变化不能证明出现故障；定义不变也不能证明安全。",
    ),
    "limit_workflows": (
        "Рабочие сценарии связаны по явно выбранным инструментам, без семантического анализа.",
        "Workflows are linked through explicitly selected tools, without semantic analysis.",
        "Los flujos se relacionan mediante herramientas seleccionadas explícitamente, sin análisis semántico.",
        "Les flux sont liés aux outils explicitement sélectionnés, sans analyse sémantique.",
        "流程仅通过明确选择的工具关联，不进行语义分析。",
    ),
    "limit_bytes": (
        "Размер компактного JSON в UTF-8 — байты, не токены, деньги или фактическая экономия.",
        "Compact JSON size in UTF-8 measures bytes, not tokens, money or achieved savings.",
        "El tamaño del JSON compacto en UTF-8 mide bytes, no tokens, dinero ni ahorro conseguido.",
        "La taille du JSON compact en UTF-8 mesure des octets, pas des tokens, de l’argent ou des économies réalisées.",
        "紧凑 JSON 的 UTF-8 大小以字节计量，不是 token 数、金额或实际节省。",
    ),
    "limit_launch": (
        "launch отображается только хешем; условия не исполняются и секреты окружения не выводятся.",
        "launch is represented only by its hash; launch conditions are not executed and environment secrets are not displayed.",
        "launch se representa solo con su hash; no se ejecutan sus condiciones ni se muestran secretos del entorno.",
        "launch n’est représenté que par son empreinte ; les conditions de lancement ne sont pas exécutées et les secrets d’environnement ne sont pas affichés.",
        "launch 仅显示哈希；不执行启动条件，也不展示环境中的秘密值。",
    ),
    "note_before_missing": (
        "До обновления нет полного успешного наблюдения.", "There is no complete successful observation before the update.",
        "No hay una observación completa y correcta anterior a la actualización.", "Aucune observation complète et réussie avant la mise à jour.",
        "缺少升级前完整且成功的观测。",
    ),
    "note_after_missing": (
        "После обновления нет полного успешного наблюдения.", "There is no complete successful observation after the update.",
        "No hay una observación completa y correcta posterior a la actualización.", "Aucune observation complète et réussie après la mise à jour.",
        "缺少升级后完整且成功的观测。",
    ),
    "note_before_target": (
        "Старый снимок относится к другой зависимости.", "The earlier snapshot belongs to a different dependency.",
        "La instantánea anterior pertenece a otra dependencia.", "L’instantané antérieur appartient à une autre dépendance.", "较早的快照属于其他依赖。",
    ),
    "note_after_target": (
        "Новый снимок относится к другой зависимости.", "The newer snapshot belongs to a different dependency.",
        "La instantánea posterior pertenece a otra dependencia.", "Le nouvel instantané appartient à une autre dépendance.", "较新的快照属于其他依赖。",
    ),
    "note_protocol_missing": (
        "Протокол одного из наблюдений неизвестен.", "The protocol of one observation is unknown.",
        "Se desconoce el protocolo de una observación.", "Le protocole d’une observation est inconnu.", "某次观测的协议未知。",
    ),
    "note_launch_missing": (
        "Условия запуска одного из наблюдений неизвестны.", "The launch conditions of one observation are unknown.",
        "Se desconocen las condiciones de arranque de una observación.", "Les conditions de lancement d’une observation sont inconnues.",
        "某次观测的启动条件未知。",
    ),
    "note_extras_unknown": (
        "Исторические annotations и другие дополнительные поля неизвестны. Сравнены только core-поля; изменения extras не определялись.",
        "Historical annotations and other extra fields are unknown. Only core fields were compared; extras changes were not determined.",
        "Se desconocen las annotations históricas y otros campos adicionales. Solo se compararon los campos básicos; no se determinaron cambios en extras.",
        "Les annotations historiques et autres champs supplémentaires sont inconnus. Seuls les champs principaux ont été comparés ; les changements d’extras n’ont pas été déterminés.",
        "历史 annotations 和其他额外字段未知。仅比较了核心字段，未判定 extras 的变化。",
    ),
    "note_order": (
        "Снимки не образуют последовательность «до → после».", "The snapshots are not in strictly increasing chronological order.",
        "Las instantáneas no están en orden cronológico estrictamente creciente.", "Les instantanés ne sont pas dans un ordre chronologique strictement croissant.",
        "快照未按严格递增的时间顺序排列。",
    ),
    "note_observed_at": (
        "{side}: время наблюдения неизвестно.", "{side}: the observation time is unknown.",
        "{side}: se desconoce la fecha de observación.", "{side} : la date d’observation est inconnue.", "{side}：观测时间未知。",
    ),
    "note_stdio_version": (
        "{side}: версия пакета для stdio-наблюдения неизвестна.", "{side}: the package version for the stdio observation is unknown.",
        "{side}: se desconoce la versión del paquete de la observación stdio.", "{side} : la version du paquet de l’observation stdio est inconnue.",
        "{side}：stdio 观测的软件包版本未知。",
    ),
    "note_selected_absent": (
        "Выбранный инструмент не найден ни в одном из двух наблюдений: {tool}", "The selected tool is absent from both observations: {tool}",
        "La herramienta seleccionada no aparece en ninguna observación: {tool}", "L’outil sélectionné est absent des deux observations : {tool}",
        "两次观测均未找到所选工具：{tool}",
    ),
    "note_metadata": (
        "{side}: {field} неизвестно; значение не подставлялось.", "{side}: {field} is missing; no value was inferred.",
        "{side}: falta {field}; no se infirió ningún valor.", "{side} : {field} manque ; aucune valeur n’a été déduite.",
        "{side}：缺少 {field}；未推断其值。",
    ),
}

MESSAGES = {key: dict(zip(SUPPORTED_LANGUAGES, values, strict=True)) for key, values in _ROWS.items()}


def message(lang: str, key: str, **values: object) -> str:
    """Format trusted built-in prose; callers escape source values for Markdown separately."""
    return MESSAGES[key][lang].format(**values)


def diagnostic(lang: str, note: str) -> str:
    """Translate generated reason forms while preserving legacy machine diagnostic values."""
    fixed = {
        "before: successful full observation missing": "note_before_missing",
        "after: successful full observation missing": "note_after_missing",
        "before: observation target differs from dependency": "note_before_target",
        "after: observation target differs from dependency": "note_after_target",
        "protocol: comparison context missing": "note_protocol_missing",
        "launch: comparison context missing": "note_launch_missing",
        "extras: coverage unknown; only core definitions compared": "note_extras_unknown",
        "observation order is not strictly chronological": "note_order",
    }
    if note in fixed:
        return message(lang, fixed[note])
    for side in ("before", "after"):
        label = message(lang, side)
        if note == side + ": observedAt missing":
            return message(lang, "note_observed_at", side=label)
        if note == side + ": packageVersion missing for stdio observation":
            return message(lang, "note_stdio_version", side=label)
        for field in ("packageVersion", "serverVersion"):
            if note == side + ": " + field + " missing; not inferred":
                return message(lang, "note_metadata", side=label, field=field)
    absent_prefix = "selected tool absent in both observations: "
    if note.startswith(absent_prefix):
        return message(lang, "note_selected_absent", tool=note[len(absent_prefix):])
    return note
