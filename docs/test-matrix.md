# Локальная матрица проверок Veilway

Полная обязательная команда для merge изменений приложения и операторских
инструментов:

```bash
./scripts/test-all.sh --build
```

Последние результаты и незавершённые ручные шаги — в [status.md](status.md).
Количество тестов в датированных планах не является текущим размером набора.

Запуск требует существующих Docker/Compose, Python 3, Node/npm, Java,
Ansible и Terraform; frontend `node_modules`, настроенного Android SDK,
кеша закреплённого Gradle/Android-зависимостей и локально подготовленных providers
для четырёх Terraform roots. Установку зависимостей, `npm ci`, подготовку SDK
и `terraform init -backend=false` оператор выполняет явно по инструкциям проекта.
При нестандартном кеше Gradle задайте `GRADLE_USER_HOME` перед запуском.
Runner не устанавливает инструменты и не инициализирует providers автоматически.

Отсутствие обязательной предпосылки или ошибка проверки возвращает ненулевой
код. ShellCheck необязателен и явно отмечается как пропущенный, если отсутствует.
Отдельный `check.sh` остаётся удобной статической проверкой с прежними сообщениями
о пропусках; его успех сам по себе не означает полную готовность к merge.

| Уровень | Содержание | Отдельная команда |
| --- | --- | --- |
| Static/unit операторских инструментов | Bash/Python, Ansible syntax, Terraform format/validate, политики планов/inventory, релиз, агенты, ignore policy и credential scan | `./scripts/check.sh` |
| Общий контракт клиентов | Один backend fixture, Pydantic/SQL/HTTP, TypeScript union-типы и потребители, Kotlin profile/polling | `node scripts/check-client-contract.mjs`; Python и Kotlin входят в строки ниже |
| Frontend unit/build | HTTP/CSRF, загрузка и состояние аналитики, геометрия графика, действия и повторы профилей, production build | `npm --prefix web/frontend test` и `npm --prefix web/frontend run build` |
| Android JVM/lint | Вход, отмена, поздние ответы, профильная политика, lint | `cd android && ./gradlew --offline testDebugUnitTest lintDebug` |
| Backend integration | Полный набор SQLite + PostgreSQL, mobile concurrency, accounting, реальные синтетические PKI/socket и CRL-agent | `./scripts/test-profile-security.sh --build` |
| Operator lifecycle | Миграции, sync-vms, PostgreSQL dump/restore и recovery metadata | `python3 scripts/control-postgres-smoke.py` |
| PKI/container | Issue/download/revoke, legacy, crash recovery, UID/socket, файловые и subprocess границы | `./scripts/test-pki-service.sh --build` |
| Browser | Синтетический API; профили, lost-response retry, аналитика, клавиатура, раскрытые детали, 1440/390/320 px | `./scripts/test-profile-panel.sh --build` |
| Native accounting | Настоящий OpenVPN в частном namespace; UUID/legacy serial mapping, status и disconnect counters | `python3 scripts/test-business-analytics-vpn.py --build` |

`test-all.sh` выполняет все строки таблицы. Полный backend и операторский
lifecycle не заменяют друг друга: второй проверяет эксплуатационный путь
миграции/sync/backup, который не доказывается обычными pytest fixtures.
`test-control-plane.sh --build` сохраняет оба этих сценария и проверку Compose.
`test-profile-api.sh --build` сохраняет отдельный вариант без PostgreSQL;
это частичная команда, не замена полного merge-набора.

Shell entrypoints делегируют фиксированным сценариям `run-tests.py`.
Одинаковый образ строится не более одного раза в одном процессе runner;
новый запуск заново выполняет build и не доверяет прежнему тегу как признаку
свежести. Стандартный Docker layer cache продолжает работать. Сборка разрешается
явным `--build`; Dockerfiles и версии образов не изменены.

PostgreSQL acceptance использует временную БД в RAM без внешней сети или
опубликованных портов. Lifecycle smoke использует отдельный уникальный Compose
project и искусственные секреты/метаданные в защищённом временном каталоге.
Сценарии сохраняют собственные ограничения capability и mounts. Cleanup
выполняется при успехе, ошибке и прерывании, только для созданных ими ресурсов.
Проверки не используют рабочие хосты, state, CA, клиентские профили или облачные API.

Android JNI/TUN и реальный Google-вход остаются отдельной приёмкой:
`scripts/test-android-tunnel.py` требует выделенного запущенного эмулятора
и использует синтетический локальный сервер. Runner не запускает эмулятор,
не подключает реальные устройства и не подтверждает ручную проверку приложения.
CI и развёртывание не добавляются этим рефакторингом.

`check.sh` также проверяет относительные ссылки и Markdown-якоря публичных
документов через `python3 scripts/check-doc-links.py`. Внешние URL не загружаются;
закрытые/игнорируемые документы и операторские файлы не читаются.
ORM baseline из `409a2ff` проверяется отдельными процессами: прежние импорты,
порядок регистрации, mapper relationships/defaults и DDL SQLite/PostgreSQL.
PostgreSQL migrations дополнительно требуют пустой Alembic metadata diff.
