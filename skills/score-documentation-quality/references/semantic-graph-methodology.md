# Семантический граф инструкций

## Решение об архитектуре

Использовать граф для любого многофайлового профиля `instruction`: в read-only режиме построить его один раз для всей области, а при последовательной переработке `AGENTS.md → DOCS.md → MODEL.md → WORKFLOW.md → contracts` применять как обязательный инкрементальный migration gate. Для одного файла граф необязателен, если пользователь не запросил глубокий смысловой анализ. Не встраивать граф в `review.json`, основной сканер или формулу 100 баллов. Граф является проверяемым analysis artifact и не становится новым источником истины.

Один навык может координировать базовую оценку и deep mode, потому что они обслуживают одну цель. Не объединять их в один монолитный скрипт: базовая оценка должна работать без RDF-хранилища, embedding-модели, сети и внешних зависимостей.

## Строить граф вместе с иерархией

При редактировании не строить итоговый граф постфактум и не добавлять будущие контракты заранее. Внутри каждого файла расширять confirmed manual graph одной line transaction: atomic rule добавлять сразу вместе с owner и scope, supporting-блок — с artifact evidence; затем независимо подтвердить delta, пересчитать изменившиеся hashes/digest и пройти migration gate до следующей единицы. Routing-link является отдельной transaction. Соблюдать тот же порядок, что и у документов:

1. рабочий агент извлекает candidate delta для `AGENTS.md`; независимый reviewer подтверждает changed nodes/edges, после чего запускается migration gate;
2. повторить pre-link/post-link цикл для `DOCS.md`: сначала подтвердить его rules/owners/scopes без parent-route, затем routing-строку `AGENTS.md → DOCS.md` и `routes_to`-ребро;
3. тем же циклом по одному добавить `MODEL.md`, затем `WORKFLOW.md`, оба из корня и с parent в `DOCS.md`;
4. добавить один ready lowercase contract из dependency DAG без parent-reference и проверить его manual delta;
5. добавить routing-строку, независимо подтверждённое `routes_to`-ребро, пересчитать hashes/digest и повторить migration gate;
6. до удаления source span либо старого пути добавить byte-exact запись в `archive/instructions.jsonl`, проверить восстановление, затем представить её отдельным immutable `source` node с `authority: false`; в node source и evidence ребра перенести возвращённые `evidence_locator/start_byte/end_byte/evidence_sha256`, чтобы checker заново хешировал точную JSONL-строку; target rule связать с записью через `derives_from`, а сам JSONL при необходимости представить одним `artifact_kind=archive`;
7. удалить или заменить stale nodes/edges, rehash затронутые parents и снова запустить migration gate;
8. только после успешного post-removal gate считать файл `content_final` и переходить к следующему ready узлу.

Model-assisted graph допускает только candidate/unknown для semantic nodes и edges. Для gate независимый reviewer создаёт отдельный manual graph из ранее подтверждённой базы и проверенного delta. Не маркировать текущего автора как `independent_agent`. `deterministic_confirmed` допустим только для результата реального воспроизводимого parser/checker. Если независимое или детерминированное подтверждение недоступно, ветвь не подключать.

Migration gate запускать с `--migration-gate`; он обязан отклонять confirmed errors, ownerless или scopeless rules, unadjudicated rule/entity nodes, edges и candidate findings. Не оставлять stale nodes, dangling edges, `references` без рабочей cross-reference или `routes_to` без routing-строки. Archive nodes имеют `artifact_kind=archive`, `authority: false` и не становятся активным owner. Промежуточные stage-графы хранить во временном каталоге; финальный граф охватывает всю принятую topology. После последнего contract запускать `--final-topology-gate`: четыре root artifacts должны иметь `artifact_kind=root_instruction`, обязательные confirmed `routes_to` должны существовать, каждый `contracts/*.md` unit — иметь contract artifact, а каждый `artifact_kind=contract` — ровно один входящий confirmed `routes_to`. Self/cyclic `routes_to` запрещены. Изменение scope digest между этапами ожидаемо, если ledger объясняет delta.

## Научная и стандартная основа

- [W3C SHACL](https://www.w3.org/TR/shacl/) разделяет data graph и shapes graph и позволяет детерминированно проверять структуру графа и формировать validation results.
- [W3C PROV-O](https://www.w3.org/TR/prov-o/) моделирует происхождение, версии, primary sources и derivation; использовать эти идеи для evidence и source edges.
- [W3C SKOS](https://www.w3.org/TR/skos-reference/) различает preferred, alternative и hidden labels; не считать похожие слова идентичными сущностями без явного mapping.
- [OWL 2 Primer](https://www.w3.org/TR/owl2-primer/) показывает, что несовместимость нужно задавать явно: reasoner не должен угадывать disjointness.
- [BCP 14 / RFC 8174](https://www.rfc-editor.org/rfc/rfc8174) различает нормативные ключевые слова и обычное употребление; для нерегламентированных инструкций фиксировать собственную шкалу `MUST/SHOULD/MAY` либо её локальный эквивалент.
- [ContractNLI](https://aclanthology.org/2021.findings-emnlp.164/) формулирует document-level NLI как entailment/contradiction/neutral с evidence spans и показывает трудность исключений и отрицаний.
- [Sentence-BERT](https://aclanthology.org/D19-1410/) позволяет быстро искать семантически похожие предложения, но cosine similarity не доказывает эквивалентность или противоречие.
- [NLP for Requirements Traceability](https://arxiv.org/html/2405.10845v1) формализует восстановление trace links как отношение между source и target artifacts.

Следствие: графовые ограничения и явно заданные связи проверяются детерминированно; извлечение скрытых сущностей, условий, исключений и paraphrase relations остаётся вероятностным или ручным.

## Модель графа

### Узлы

| Тип | Назначение |
|---|---|
| `artifact` | Версионированный документ с `artifact_kind=root_instruction|contract|archive|supporting` |
| `rule` | Одно decision-changing нормативное утверждение |
| `entity` | Сущность с canonical name и aliases |
| `scope` | Область применимости |
| `source` | Первичный или производный источник authority/evidence |

Для `artifact` и `source` явно задавать `authority: true|false`. `root_instruction` и `contract` требуют `authority: true`, `archive` — `authority: false`. Рёбра `owns` и `precedes` допустимы только для узлов с `authority: true`; самоназвание файла authority не является достаточным основанием.

Нормализовать `rule` полями:

- `subject` — кто обязан или может действовать;
- `modality` — `MUST`, `SHOULD`, `MAY` или `FACT`;
- `polarity` — `positive` или `negative`;
- `action` и `object`;
- `condition` и `exception`;
- точный source span.

Каждый `rule` и `entity` обязан иметь node-level provenance: `assertion`, `status`, `method`, `adjudication` и `adjudicator`. Не считать claim, canonical name или aliases истинными только потому, что их создала модель. `model_assisted` nodes остаются `candidate|unknown`; migration gate принимает только независимо `manual_confirmed` либо воспроизводимо `deterministic_confirmed` semantic nodes.

### Рёбра

| Тип | Значение |
|---|---|
| `owns` | source/artifact является canonical owner правила |
| `precedes` | один authority имеет явный приоритет над другим |
| `applies_to` | правило действует в scope |
| `overrides` | правило явно переопределяет другое |
| `derives_from` | правило или artifact производен от source |
| `aliases` | имя является alias canonical entity |
| `references` | artifact или rule ссылается на другой узел |
| `routes_to` | canonical parent маршрутизирует к одному дочернему artifact |

Каждое ребро содержит `assertion = explicit|inferred`, `status = candidate|confirmed|rejected|unknown`, `method`, `adjudication`, `adjudicator {kind,id,note}` и evidence. Поля adjudicator являются provenance-заявлением, а не аутентификацией личности: локальный checker не имеет права выдавать их за доверенный gate. Граф с `producer=model_assisted` может содержать только `candidate|unknown`. Inferred edge никогда не получает `confirmed`: после независимого чтения источников ревьюер создаёт новое `explicit + manual_confirmed` ребро в отдельном manual graph с прямыми source spans, а исходный candidate остаётся candidate либо `manual_rejected`. `candidate|unknown` обязаны иметь `not_reviewed` и `adjudicator.kind=none`. `manual_confirmed|manual_rejected` требуют `human|independent_agent`; `deterministic_confirmed` — `deterministic_checker`. `explicit + deterministic_confirmed` допустимо только для воспроизводимого детерминированного parse.

Одинаково нормализованные rule-ключи с противоположной polarity или различной modality остаются отдельными candidate findings даже в manual graph; polarity не подавляет modality drift. Для снятия каждого добавить собственный `finding_resolutions`: точный finding code, отсортированные node IDs, выданный checker `candidate_fingerprint`, решение `rejected|resolved`, независимого adjudicator и прямые evidence. Fingerprint связывает решение с выбранными и непосредственно связанными nodes, incident edges/evidence и hashes только затронутых scope units. Для immutable span/archive record использовать `evidence_sha256`, чтобы последующая запись в тот же JSONL не аннулировала решение; изменение самой записи/связанного контекста делает resolution stale, независимая ветвь — нет. `resolved` допустим только при confirmed `overrides` для каждой различающейся пары; directed override cycles блокируются отдельно. `rejected` означает независимо доказанное ложное совпадение. Не менять статус candidate простым редактированием отчёта.

## Детерминированные проверки

Проверять без semantic model:

- SHA-256 каждого in-scope artifact и общий scope digest;
- отсутствие абсолютных путей, `..`, symlinks и чтения вне scope;
- canonical root/contract paths и согласованность `artifact_kind`;
- уникальность IDs и существование концов каждого ребра;
- один confirmed owner каждого rule;
- циклы confirmed `precedes` и `derives_from`;
- правила без owner;
- коллизии canonical entity names и неоднозначные aliases;
- одинаково нормализованные rule-ключи с противоположной polarity как candidates;
- изменение modality одного rule-ключа как candidate drift.
- на финальном gate — обязательные root routes и ровно один canonical parent каждого contract.

Отсутствие найденного конфликта не доказывает семантическую согласованность.

## Вероятностные проверки

Использовать модель, embeddings или NLI только для кандидатов:

- coreference и скрытый subject;
- являются ли два имени одной сущностью;
- семантические дубли и paraphrases;
- пересечение естественно-языковых conditions;
- отрицания, исключения и exception shadowing;
- сохранение смысла при переносе или сокращении правила;
- contradiction/entailment между разными формулировками.

Каждый candidate обязан сохранить source spans и тип метода (`coreference`, `entity_linking`, `embedding`, `nli`, `llm` или `manual_analysis`). Не использовать model confidence как балл. До `manual_confirmed` candidate не меняет статус критерия и не создаёт cap.

## Связь с рубрикой

| Finding графа | Критерий или cap после adjudication |
|---|---|
| duplicate owner, circular authority | `owner_source`, `single_owner`, при конфликте `critical_authority_conflict` |
| duplicate artifact path | `identity_hierarchy`, `single_owner`, `duplication_control` |
| noncanonical root/contract path или incomplete final topology | `identity_hierarchy`, `navigation`, `portability`; при сломанной ссылке также `links_anchors` |
| rule without scope | `purpose_scope` и/или `audience_applicability` с evidence `gap` |
| precedence/override cycle, unresolved override | `precedence_boundaries`, `cross_document_consistency` |
| routing cycle или неверный canonical parent | `identity_hierarchy`, `navigation`, `ordered_actions` |
| derivation cycle | `primary_source_alignment`, `lifecycle_change` |
| entity collision, ambiguous alias | `entity_terminology_consistency` |
| normalized polarity conflict | `internal_consistency` или `cross_document_consistency`; при основном пути `operational_contradiction` |
| normalized modality drift | `normative_language` и `internal_consistency`/`cross_document_consistency` |
| missing primary trace при доступной области | `primary_source_alignment = unmet` либо `partial` с evidence `gap` |
| named primary source недоступен | `primary_source_alignment = unknown` с evidence `unavailable` |
| confirmed source mismatch | `primary_source_alignment = unmet` и при material impact `material_primary_source_mismatch` |
| orphan rule | `single_owner` и при необходимости `owner_source`; сначала проверить, не является ли правило локальным |

Не применять cap автоматически из отчёта checker. Перенести подтверждённое evidence в review вручную и объяснить влияние.

## Когда разделить на другой навык

Оставлять deep mode внутри этого навыка, пока граф создаётся для одной ограниченной оценки. Выделять самостоятельный навык или сервис, если требуется постоянное графовое хранилище, межрепозиторный индекс, embedding/NLI runtime, массовое обновление графа или отдельный lifecycle/owner. Такое разделение требует явного запроса и не должно создавать второй editable authority.
