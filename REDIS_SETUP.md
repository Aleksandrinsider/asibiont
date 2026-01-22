# Redis Setup для Production

## Установка Redis

### Windows (через Chocolatey)
```powershell
choco install redis-64
```

### Linux/Ubuntu
```bash
sudo apt update
sudo apt install redis-server
sudo systemctl start redis-server
sudo systemctl enable redis-server
```

### macOS
```bash
brew install redis
brew services start redis
```

## Проверка работы
```bash
redis-cli ping  # Должен вернуть PONG
```

## Конфигурация (опционально)
Создать `/etc/redis/redis.conf`:
```
maxmemory 256mb
maxmemory-policy allkeys-lru
tcp-keepalive 300
```

## Перезапуск
```bash
sudo systemctl restart redis-server
```

## Мониторинг
```bash
redis-cli info stats
redis-cli monitor  # Просмотр всех операций
```

## Производительность
- **Без Redis**: ~1μs на операцию кэша
- **С Redis**: ~100-500μs на операцию
- **Экономия API**: до 90% повторяющихся запросов

## Когда использовать Redis
- Production среда
- >100 запросов в секунду
- Масштабирование на несколько серверов
- Необходимость персистентности кэша