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

## Render (бесплатный Web Service)

На [Render](https://dashboard.render.com/) бот работает как **Web Service** (Free): поднимает HTTP на `$PORT`, отдаёт `/health` и принимает апдейты Telegram через webhook.

Ограничения бесплатного тарифа ([документация Render](https://render.com/docs/free)):

- сервис засыпает примерно через **15 минут без HTTP-запросов**;
- первый запрос после сна будит его около минуты — первое сообщение в Telegram может прийти с задержкой;
- на workspace даётся **750 бесплатных часов в месяц** (этого хватает на один сервис почти круглосуточно, если его не усыплять).

Чтобы реже засыпал, повесьте бесплатный монитор (например [cron-job.org](https://cron-job.org/) или UptimeRobot) на `https://<имя>.onrender.com/health` каждые 10 минут.

Если уже создан Background Worker — удалите или остановите его. Одновременно с Web Service он даст конфликт Telegram.

Локальный `python -m app.main` на время работы Render тоже остановите.

### Как создать сервис в Dashboard

1. Откройте [dashboard.render.com](https://dashboard.render.com/), войдите через GitHub.
2. **New +** → **Web Service**.
3. Репозиторий `Gregimuri/ADTS_Assistant_Manager_bot`, ветка `main`.
4. Заполните:
   - **Name:** `adts-assistant-manager-bot`
   - **Region:** Frankfurt
   - **Runtime:** Python
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python -m app.main`
   - **Instance type:** Free
5. В **Environment** добавьте `BOT_TOKEN`.
6. **Create Web Service** и дождитесь **Live**.
7. Откройте `https://<имя>.onrender.com/health` — должно быть `ok`.
8. В Telegram: `/start` или `#СчетЕММ`.

Либо **New +** → **Blueprint**: подхватится [`render.yaml`](render.yaml), Render спросит `BOT_TOKEN`.
