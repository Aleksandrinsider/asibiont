# CLI Утилита для Управления Системой Самоисправления

## Обзор

CLI утилита `self_healing_cli.py` предоставляет удобный интерфейс командной строки для управления системой самоисправления AI агента.

## Установка

Утилита не требует дополнительной установки - она работает с существующей кодовой базой.

## Использование

```bash
python self_healing_cli.py <command> [options]
```

## Доступные Команды

### status
Показывает текущий статус системы самоисправления.

```bash
python self_healing_cli.py status
```

Вывод включает:
- Статус мониторинга
- Количество снимков системы
- Метрики здоровья
- Историю версий

### start
Запускает мониторинг системы.

```bash
python self_healing_cli.py start [--daemon]
```

Опции:
- `--daemon`: Запуск в режиме демона (фоновый режим)

### stop
Останавливает мониторинг системы.

```bash
python self_healing_cli.py stop
```

### snapshot
Создает снимок текущего состояния системы.

```bash
python self_healing_cli.py snapshot [--broken]
```

Опции:
- `--broken`: Помечает снимок как "сломанный" (по умолчанию - "работающий")

### rollback
Выполняет откат системы к предыдущему состоянию.

```bash
python self_healing_cli.py rollback <type>
```

Типы отката:
- `code`: Откат только кода
- `config`: Откат только конфигурации
- `all`: Откат кода и конфигурации

### restart
Перезапускает сервис.

```bash
python self_healing_cli.py restart
```

### clean
Очищает старые резервные копии.

```bash
python self_healing_cli.py clean
```

### test
Запускает комплексное тестирование системы.

```bash
python self_healing_cli.py test
```

## Примеры Использования

### Мониторинг Системы

```bash
# Запуск мониторинга в фоне
python self_healing_cli.py start --daemon

# Проверка статуса
python self_healing_cli.py status

# Остановка мониторинга
python self_healing_cli.py stop
```

### Работа со Снимками

```bash
# Создание рабочего снимка
python self_healing_cli.py snapshot

# Создание снимка сломанной версии
python self_healing_cli.py snapshot --broken

# Откат кода при проблемах
python self_healing_cli.py rollback code

# Откат всего
python self_healing_cli.py rollback all
```

### Обслуживание

```bash
# Очистка старых бэкапов
python self_healing_cli.py clean

# Перезапуск сервиса
python self_healing_cli.py restart

# Полное тестирование
python self_healing_cli.py test
```

## Интеграция с Системой

CLI утилита интегрируется с основными компонентами:

- **SelfHealingAgent**: Основной класс системы самоисправления
- **AsyncAgentLogger**: Система логирования для метрик
- **System Snapshots**: Управление версиями кода и конфигурации

## Автоматизация

Утилиту можно использовать в скриптах автоматизации:

```bash
#!/bin/bash
# Ежедневная проверка и очистка
python self_healing_cli.py clean
python self_healing_cli.py status
```

## Мониторинг и Оповещения

Система автоматически:
- Отправляет оповещения администратору при проблемах
- Создает снимки при изменениях
- Выполняет откат при обнаружении ошибок

## Безопасность

- Все операции логируются
- Резервные копии защищены от случайного удаления
- Откат возможен только к рабочим версиям

## Troubleshooting

### Мониторинг не запускается
```bash
python self_healing_cli.py status
# Проверьте логи на наличие ошибок
```

### Снимки не создаются
```bash
python self_healing_cli.py test
# Запустите тестирование для диагностики
```

### Откат не работает
```bash
python self_healing_cli.py status
# Убедитесь, что есть рабочие снимки
```