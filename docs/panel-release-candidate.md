# Кандидат обновления панели: M4.1

Дата: 2026-10-07. Статус: **M4.1 DONE — локальный кандидат проверен**.

Цель первой итерации — зафиксировать состав обновления существующей панели
и проверить его локально. Это не rollout и не разрешение на изменения VM.
План последовательных итераций — [M4](pki-evolution-plan.md#m4-production-перенос).

## Состояние исходников

Базовый `HEAD`: `fc6a3c9` (`refactor: organize Ansible roles into static task
phases (#5)`). Изменения Google/profile/PKI и migration M1–M3 находятся в рабочем
дереве: есть tracked modifications и новые untracked files. Staged diff пуст.
`HEAD` не идентифицирует кандидата. Изменения оператора в `AGENTS.md` сохранены:
добавлены правила PR/squash merge; они не меняют runtime приложения.

Полный состав публичных исходников кандидата и SHA-256 каждого файла —
[манифест](panel-release-candidate.json). Он исключает private inventory,
`.env`, CA, профили, build output, caches и сам отчёт/манифест. Контрольные суммы
описывают локальные исходники, а не опубликованные образы. Перед rollout нужны
reviewed revision, сверка файлов с кандидатом и фиксация фактических image IDs/
digests в защищённом операторском журнале. Теги `:0.1.0` сами по себе версию
не доказывают; deployment сейчас собирает образы на VM с `--pull`.

Манифест содержит 226 публичных файлов, в том числе 79 runtime inputs панели.
Runtime SHA-256: `b41631f4c9a36f4b249ef48a5b1c9cae7020883da39dad6459a0e9501abb7529`.
Группы `panel_deployment` и `node_and_support_deployment` раздельны: наличие
файлов второго этапа не означает выполнение node deployment при web apply.
Generated caches/egg-info игнорируются Git и не входят в source manifest;
для production использовать чистый checkout reviewed revision.

## Состав и поведение обновления

| Часть | Что входит | Изменение поведения |
| --- | --- | --- |
| Backend/API | Google OIDC, роли/ownership, profiles/jobs, CRL publisher, legacy sync | Вход через Google; USER скачивает только своё; ADMIN назначает/отзывает |
| PostgreSQL | Alembic 0002–0006 поверх 0001 | История сохраняется; старые сессии отключаются; появляется provenance импорта |
| Frontend/Caddy | ADMIN/USER кабинеты, история/узлы/CRL, no-store и callback handling | Парольный вход заменён Google; импорт виден в истории |
| PKI | Отдельный изолированный контейнер, Unix socket, import-ca/import-profiles | Тот же CA и исходные `.ovpn`; без перевыпуска, новые owners задаёт ADMIN |
| Panel deployment | control-web role, allowlisted build inputs, secrets и persistent storage | Останавливает API/web и имеющийся PKI перед миграцией; сохраняет DB |
| Operator tools | Защищённый wrapper и local writer handover | `.env` читается операторским wrapper; после handover локальная выдача блокируется |
| VPN CRL stage | Agent, managed mounts и guard от старого local CRL | Отдельная операция на узлах после явного согласования |

Panel apply устанавливает зависимости и настраивает dedicated data disk,
mount/tmpfiles, создаёт Docker networks, собирает образы, применяет миграции, синхронизирует
VM/token hashes и запускает API/web/PKI. Он не импортирует CA/профили автоматически
и не переключает CRL mounts VPN-узлов. PKI без CA остаётся недоступным для выдачи.
Последовательность импорта — [инструкция](legacy-profile-migration.md).
Нельзя выдавать доступ до проверки публикации/receipts и старых подключений.

CA, server/transit identities, wrapping keys, endpoints и исходные клиентские
файлы сохраняются. Нужные CRL config/container changes согласуются отдельно;
existing OpenVPN Access Server остаётся без изменений. Ротация/создание/удаление
CA через ADMIN-панель — будущие C1–C4, не часть этого кандидата.

## Приёмка и оставшиеся gates

| Проверка 2026-10-07 | Результат |
| --- | --- |
| `scripts/check.sh` | PASS: Bash/Python, 4 Terraform validate, 7 Ansible syntax checks, Compose isolation, frontend typecheck/13 tests, ignore policy и diff scan |
| ShellCheck | SKIPPED: не установлен; не устанавливался |
| `scripts/test-pki-service.sh --build` | PASS: 27 PKI/container tests, реальный privilege-drop entrypoint, synthetic legacy import |
| `scripts/test-control-plane.sh --build` | PASS: 173 backend/integration tests с temporary PostgreSQL, без пропусков; 2 сторонние deprecation warnings |
| Frontend build в test-control-plane | PASS: 13 tests и production build из текущих исходников |
| Production API image / PostgreSQL smoke | PASS: миграции до 0006, VM sync и реальный pg_dump/pg_restore, сохранение владельца/provenance/audit/CRL receipt |
| Credential scan публичных файлов и reachable history | PASS, 593 Git objects; секретные данные не выводились |
| Git whitespace / ссылки / manifest hashes | PASS; staged diff пуст |

Не проверялись реальные Google credentials, SSH, данные VM/диска, CA/profile
materials и live VPN: первая итерация ограничена публичными исходниками и
синтетическими временными fixtures.

Перед изменениями VM отдельно требуются reviewed release через PR, защищённые
inventory/input paths и согласованный backup. Для M4.3–M4.5 нужны точные
разрешения на export/handover, остановку панели, импорт и изменения CRL на
выделенных узлах по [runbook](profile-rollout.md) и `AGENTS.md`.

Следующая маленькая итерация **M4.2** — определить защищённое место backup,
подтвердить нужный inventory и согласовать операции/окно обслуживания.
Commit/PR/push/apply/export/handover в M4.1 не выполнялись.

## Оформление версии после M4.1

2026-10-07: по указанию оператора подготовлена ветка
`feat/google-profile-panel` для draft PR в `main`. Состояние Git выше описывает
момент M4.1; публикация кандидата не означает review, merge или rollout.
Runtime fingerprint остался прежним; прошедшие проверки относятся к тому же
составу runtime. Секретные backup/import артефакты находятся вне репозитория.

Код кандидата зафиксирован коммитом `9241db4` и опубликован в
[draft PR #6](https://github.com/shcherbatkin-pavel/veilway/pull/6).
Последующие изменения журнала не меняют runtime. Перед rollout использовать
финальную reviewed/merged revision PR, а не базовый HEAD M4.1 или название тега.
