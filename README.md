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

## Render (работа 24/7)

Бот слушает Telegram через long polling, без HTTP. На [Render](https://dashboard.render.com/) его нужно запускать как **Background Worker**, а не Web Service.

- Worker не засыпает и не требует открытый порт.
- У Worker нет бесплатного тарифа. Постоянная работа — план **Starter**, около [$7/мес](https://render.com/pricing).
- Бесплатный Web Service засыпает через ~15 минут без запросов, поэтому для этого бота не подходит.

Пока бот крутится на Render, локальный `python -m app.main` нужно остановить: иначе Telegram вернёт ошибку `409 Conflict`.

### Как создать сервис в Dashboard

1. Откройте [dashboard.render.com](https://dashboard.render.com/), войдите через GitHub.
2. **New +** → **Background Worker**.
3. Подключите репозиторий `Gregimuri/ADTS_Assistant_Manager_bot`, ветка `main`.
4. Заполните:
   - **Name:** `adts-assistant-manager-bot`
   - **Region:** Frankfurt
   - **Runtime:** Python
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python -m app.main`
   - **Instance type:** Starter
5. В **Environment Variables** добавьте `BOT_TOKEN` (тот же токен, что в локальном `.env`). Остальные переменные уже есть значения по умолчанию в коде, их можно не дублировать.
6. Нажмите **Create Background Worker** и дождитесь статуса **Live**.
7. В Telegram отправьте `/start` или `#СчетЕММ`.

Либо **New +** → **Blueprint**, укажите тот же репозиторий: подхватится [`render.yaml`](render.yaml), а `BOT_TOKEN` Render спросит при создании.
