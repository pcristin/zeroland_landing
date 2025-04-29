# ZeroLend USDC Depositor

Скрипт для автоматизации размещения USDC на платформе ZeroLend.

## Установка

1. Клонируйте репозиторий:
```
git clone https://github.com/your-username/zeroland_landing.git
cd zeroland_landing
```

2. Установите зависимости:
```
pip install -r requirements.txt
```

3. Создайте файл `.env` в корневой директории проекта:
```
PRIVATE_KEYS={"my_wallet_key":"ВАШ ПРИВАТНЫЙ КЛЮЧ"}
PROXIES={"my_proxy":"ваш http прокси в формате login:pass@host:port (если нет прокси, оставьте пустым)"}
```

## Настройка

Отредактируйте файл `config/settings.json`:

```json
{
  "proxy": "ENV:my_proxy",
  "private_key": "ENV:my_wallet_key",
  "token": "USDC",
  "network": "LINEA",
  "amount": 0.25
}
```

- `amount`: количество USDC для депозита (минимум 0.00001)
- `network`: сеть для работы (поддерживается LINEA)

## Запуск

```
python main.py
```

## Поддерживаемые сети

- LINEA (с использованием USDC)