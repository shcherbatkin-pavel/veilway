# Veilway

Veilway — VPN-сервис для самостоятельного размещения: Direct через Yandex Cloud
или AWS, multi-hop с входом через Yandex и выходом через AWS, публичная панель
Google-входа, профилей и перезапусков, Android-клиент для выбранных приложений.
OpenVPN 2.6 работает на выделенных Ubuntu-ВМ; серверная PKI изолирована от сети.

[Текущее состояние](docs/status.md) разделяет слитую реализацию, автоматическую
приёмку, ручные проверки и последнее документированное развёртывание.
[Дорожная карта](docs/roadmap.md) описывает порядок развития, а датированные
планы и ADR сохраняют историю решений.

## Разработка и архитектура

- [Требования](docs/requirements.md) и [правила участников](AGENTS.md).
- [Матрица локальных проверок](docs/test-matrix.md): `./scripts/test-all.sh --build`
  перед merge, `./scripts/check.sh` для доступных статических проверок.
- [ADR 0001: Direct](docs/adr/0001-greenfield-direct-vpn.md),
  [0002: отклонённый UDP/443](docs/adr/0002-aws-direct-udp-443.md),
  [0003: multi-hop](docs/adr/0003-yc-aws-fail-closed-multihop.md).
- [ADR 0004: перезапуски](docs/adr/0004-public-restart-control-plane.md),
  [0005: Google/профили/PKI](docs/adr/0005-google-profiles-and-server-pki.md),
  [0006: границы модулей и ORM](docs/adr/0006-domain-models-and-refactoring-boundaries.md).
- [История Google/профилей](docs/profile-management-plan.md),
  [план миграции и CA](docs/pki-evolution-plan.md),
  [исторический кандидат M4.1](docs/panel-release-candidate.md).

## Приложения и руководства

- [Панель профилей](docs/profile-panel.md) и [API профилей](docs/profile-api.md).
- [Перезапуски и Google-вход](docs/control-plane.md),
  [обзор здоровья](docs/observability.md), [бизнес-аналитика](docs/business-analytics.md).
- [Android: сборка и пилот](docs/android-client.md),
  [установка и ручная проверка](docs/android-pilot-user.md).
- [Сервис PKI](docs/pki-service.md), [доставка CRL](docs/crl-delivery.md),
  [измерения на искусственном CA](docs/pki-benchmark.md).

## Операции

- [VPN: Terraform/Ansible и приёмка](docs/deployment.md).
- [Панель: передача управления, копирование и восстановление](docs/profile-rollout.md).
- [Существующие профили без перевыпуска](docs/legacy-profile-migration.md).
- [Безопасность и отдельная реальная приёмка](docs/profile-security-acceptance.md).
- [Точечный выпуск API/web: условия и исторический пример](docs/api-web-rollout.md).

Разработка и тесты используют искусственные данные и не разрешают операции
на рабочих хостах. Применение Terraform/Ansible, подключение к ВМ, перенос CA,
настройка Google и перезапуски требуют отдельного точного разрешения оператора.
Существующий OpenVPN Access Server остаётся вне Veilway и без изменений.

PKI, профили, `.env`, инвентари, Terraform state, подписанные APK и диагностика
исключены из Git. Не публикуйте секреты или инфраструктурные данные. Сканер
публичных файлов выводит только пути совпадений и остаётся эвристикой.

## Лицензия

Код Veilway — [MIT](LICENSE); [русский перевод](docs/license-ru.md) для ознакомления.
[Уведомления Android-зависимостей](android/third-party/NOTICE.md) описывают
лицензии библиотек и передачу соответствующих исходников вместе с APK.
