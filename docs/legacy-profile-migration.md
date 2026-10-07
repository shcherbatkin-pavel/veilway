# Миграция существующих VPN-профилей

Сохраняются действующий Veilway CA и исходные `.ovpn`. Импорт не выпускает
сертификаты, не меняет ключи, remote или сроки и не заменяет server/transit
identities на VPN-узлах. OpenVPN Access Server не затрагивается. Выполнение
production-команд требует точного согласования по `AGENTS.md` и
[runbook](profile-rollout.md).

## Контракт

CA импортируется отдельно по прежнему allowlist. Для клиентских материалов
предусмотрен отдельный `import-profiles`. После разрешения на чтение/копирование
реальных профилей подготовить согласованный backup CA, профилей и базы и
остановить локальные writers. Исходные файлы находятся в `client-profiles/*.ovpn`:
старый CLI удалял отдельные клиентские ключи после генерации, но сохранил их
внутри `.ovpn`. Отдельный client certificate не позволяет восстановить ключ.

В защищённом каталоге вне Git/build context подготовить только `manifest.json`
и явно перечисленные `.ovpn`. Каталоги `0700`, файлы `0600`; на VM numeric
owner 10002. Symlinks/hardlinks запрещены. Имена файлов — ASCII без путей,
до 133 символов включая `.ovpn`. В комплекте 1–1000 файлов; для большего
количества использовать несколько комплектов. Пример с вымышленными именами:

```json
{
  "version": 1,
  "profiles": [
    {"file": "laptop-yc-direct.ovpn", "mode": "yc-direct", "device_name": "Laptop"},
    {"file": "phone-aws-direct.ovpn", "mode": "aws-direct", "device_name": "Phone"},
    {"file": "tablet-yc-aws-multihop.ovpn", "mode": "yc-aws-multihop", "device_name": "Tablet"}
  ]
}
```

Владельцев ADMIN назначает зарегистрированным USER в панели после sync.
Имя файла, CN и название устройства не определяют Google identity. Материалы
не передаются через браузер/API/stdin бэкенда. В PostgreSQL попадают только
UUID, имя, режим, даты, состояние, serial и fingerprints. Пароль и credentials
хранятся отдельно от backup.

Импорт проверяет:

- Точный набор безопасных директив Veilway и четыре inline блока `ca`, `cert`,
  `key`, `tls-crypt-v2`. Порядок/комментарии допустимы; дубли, plugins, скрипты,
  внешние файлы и дополнительные remotes отклоняются. Конфигурация не запускается.
- Совпадение CA, подписанного сертификата с `newcerts`/реестром, serial/срока,
  clientAuth, CA:false и приватного ключа с сертификатом.
- Аутентичность wrapped `tls-crypt-v2` относительно server key нужного режима.
  Формат сверён с [OpenVPN 2.6](https://github.com/OpenVPN/openvpn/blob/v2.6.12/src/openvpn/tls_crypt.c).
- Endpoint согласно настройкам режима. Исходный AWS Direct port 443 разрешён
  наряду с 1194, без переписывания; доступность исходного порта проверяется live.

Сохраняются все исходные байты `.ovpn`. Истёкшие/отозванные профили импортируются
как история и не скачиваются; CRL не меняется. Нестандартный или неполный файл
отклоняется без перевыпуска и без изменения старого клиентского доступа. Для
такого формата требуется отдельное расширение контракта, а не ручная подмена
материалов. UUID определяется сертификатом и CA. Повтор под новым именем или
режимом конфликтует; один serial не создаёт несколько профилей.

## Операторский порядок

1. Выполнить frozen backup, handover и импорт **того же** CA по runbook.
   Server/transit certificates и wrapping keys на узлах сохранить. Завершить
   Google-вход ADMIN и получить его UUID из `/api/v1/auth/session`; не выводить
   cookie/CSRF/credentials в журналы.
2. Отдельно согласовать остановку `api web pki`, закрывая все серверные writers.
   Локальные writers уже заблокированы handover. VPN остаётся работать.
3. Выполнить импорт из подготовленного приватного каталога:

   ```bash
   docker compose --file /opt/veilway-control/app/compose.yaml stop api web pki
   docker compose --file /opt/veilway-control/app/compose.yaml run --rm --no-deps \
     --volume /private/approved-legacy-profiles:/import:ro \
     pki import-profiles --source /import
   docker compose --file /opt/veilway-control/app/compose.yaml up --detach pki
   ```

4. При остановленных API/web синхронизировать метаданные по Unix socket.
   Ниже заменить вымышленный UUID фактическим UUID закреплённого Google ADMIN:

   ```bash
   docker compose --file /opt/veilway-control/app/compose.yaml run --rm --no-deps \
     api veilway-control import-legacy-profiles \
     --admin-id 00000000-0000-4000-8000-000000000001
   ```

   Каталог постраничный, привязан к PKI generation; смена поколения вызывает
   отказ. Вся пачка метаданных фиксируется одной транзакцией. Новые signing
   jobs не создаются; до sync импортированные материалы не видны в панели.
5. Проверить коды завершения, затем отдельно согласованно запустить `api web`.
   ADMIN назначает владельцев через существующий экран. USER видит сроки и
   скачивает свои действующие профили. В истории есть событие импорта.
6. До открытия выдачи выполнить live-приёмку исходных клиентских файлов для
   всех режимов и CRL gates. Сверить сертификаты/ключи/сроки/downloaded bytes
   в защищённой среде. Для теста отзыва использовать отдельный временный профиль,
   не существующий пользовательский доступ. Взять coherent backup после переноса.

## Сбой и восстановление

PKI фиксирует комплект атомарно сменой `CURRENT`. Ошибка до commit не публикует
профили и не меняет реестр/CRL/счётчики. Сбой после commit восстанавливается
повтором того же комплекта. Следующая транзакция удаляет только служебные
неопубликованные поколения.

После успешного PKI import и неудачного sync повторить sync при закрытой для
записи панели. Он не сбрасывает имена, владельцев и состояние уже запущенного
отзыва. Не откатывать CA, не создавать второй профиль и не перевыпускать
сертификат для исправления ошибки базы.

Backup включает полный PKI generation с исходными client materials/records/
receipts и PostgreSQL с владельцами, provenance, аудитом и jobs. Восстанавливать
согласованную пару по runbook без уменьшения счётчиков, потери отзывов и сброса
CRL-agent rollback protection. Миграция `0006_legacy_profiles` блокирует
downgrade при наличии импортированных записей или событий.

## Локальные проверки

```bash
./scripts/test-pki-service.sh --build
./scripts/test-profile-security.sh --build
python3 scripts/control-postgres-smoke.py
./scripts/check.sh
```

Только временные синтетические CA/профили/БД; реальный rollout и приёмка
остаются отдельными операторскими действиями.
