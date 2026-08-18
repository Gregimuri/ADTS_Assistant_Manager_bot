# Помощник менеджера

Telegram-бот для менеджеров. Сейчас умеет собирать текст счёта по тегу `#СчетЕММ` из Google-таблицы [ЕММ (Список плееров)](https://docs.google.com/spreadsheets/d/1sRy5VuFGEWsh_RZCGUkWKyKcLP8i6dO-F1smi4ok4Mk/edit?gid=0#gid=0).

## Что делает `#СчетЕММ`

Сообщение:

```
#СчетЕММ
Начинатель
Сахарозаводчица
Грено
Гуммель
```

Бот ищет каждую ТТ в колонке `name` (как целое слово), группирует плееры по `objectNumber` и считает работы:

- ЕММ — все устройства, кроме статуса `не ставить`
- прошивки — `Перепрошить = прошить`
- кубик — `Обновить Кубик = обновить` (выезд; удалённые статусы не считаются)

Цена: `1000 + 500 × (ЕММ + прошивки + кубики)`.

## Запуск

Нужен Python 3.10+.

1. Создайте бота у [@BotFather](https://t.me/BotFather) и скопируйте токен.
2. В корне проекта:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

3. В `.env` укажите `BOT_TOKEN`.
4. Запустите:

```bash
python -m app.main
```

Таблица открыта на чтение, отдельный Google-аккаунт не нужен. Данные кэшируются на 10 минут (`SHEETS_CACHE_TTL_SECONDS`).

## GitHub

Репозиторий: https://github.com/Gregimuri/ADTS_Assistant_Manager_bot

Файл `.env` с токеном в git не попадает — на GitHub только `.env.example`.
