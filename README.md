# Business Automation Bridge

**Система автоматизации обработки клиентских заявок на Python с интеграцией Telegram и Google Sheets.**

## О проекте

Business Automation Bridge — приложение для автоматизации сбора, хранения и обработки заявок клиентов.

Система объединяет веб-форму, базу данных SQLite, Telegram-уведомления и Google Sheets. Предусмотрена двусторонняя синхронизация данных с обнаружением и разрешением конфликтов.

## Основные возможности

- Приём заявок через веб-форму.
- Сохранение заявок в SQLite.
- Уведомления о новых заявках через Telegram.
- Двусторонняя синхронизация SQLite и Google Sheets.
- Автоматическая синхронизация каждые 30 секунд.
- Административная панель с HTTP Basic Authentication.
- Просмотр и изменение статусов заявок.
- Обнаружение конфликтов при синхронизации.
- Ручное разрешение конфликтов через админ-панель.
- Журнал событий синхронизации.

## Технологии

- Python
- FastAPI
- SQLite
- Google Sheets API
- Telegram Bot API
- gspread
- HTTPX
- HTML / CSS
- Pydantic

## Структура проекта

```text
business-automation-bridge/
├── app/
│   ├── main.py
│   ├── auth.py
│   ├── routers/
│   │   └── leads.py
│   ├── services/
│   │   ├── telegram.py
│   │   ├── google_sheets.py
│   │   ├── sync_engine.py
│   │   └── sync_scheduler.py
│   ├── database/
│   │   ├── db.py
│   │   ├── models.py
│   │   └── sync_state.py
│   └── templates/
│       ├── index.html
│       └── admin.html
├── credentials/
│   └── .gitkeep
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
├── resolve_conflicts.py
├── test_conflicts.py
├── test_sheets.py
└── test_sync.py
```

## Установка и запуск

### 1. Клонирование репозитория

```bash
git clone https://github.com/soulwound/business-automation-bridge.git
cd business-automation-bridge
```

### 2. Создание виртуального окружения

Windows:

```powershell
python -m venv .venv
.venv\Scripts\activate
```

Linux / macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Установка зависимостей

```bash
pip install -r requirements.txt
```

### 4. Настройка переменных окружения

Создайте файл `.env` на основе `.env.example`.

Заполните необходимые параметры:

- `BOT_TOKEN` — токен Telegram-бота.
- `ADMIN_ID` — Telegram ID администратора.
- `SPREADSHEET_ID` — идентификатор Google-таблицы.
- `ADMIN_USERNAME` — логин администратора.
- `ADMIN_PASSWORD` — пароль администратора.

### 5. Настройка Google Sheets

1. Создайте проект в Google Cloud.
2. Включите Google Sheets API.
3. Создайте Service Account.
4. Скачайте JSON-ключ сервисного аккаунта.
5. Поместите файл в `credentials/service_account.json`.
6. Предоставьте сервисному аккаунту доступ к нужной Google-таблице.

### 6. Запуск приложения

```bash
uvicorn app.main:app --reload
```

После запуска доступны:

| Раздел | Адрес |
|---|---|
| Веб-форма | http://127.0.0.1:8000/ |
| Админ-панель | http://127.0.0.1:8000/admin |
| Проверка состояния | http://127.0.0.1:8000/health |

## Синхронизация данных

Приложение поддерживает двустороннюю синхронизацию между SQLite и Google Sheets.

Автоматическая синхронизация выполняется каждые 30 секунд.

При обнаружении одновременного изменения одного поля в обеих системах создаётся конфликт.

Администратор может выбрать источник данных:

- SQLite;
- Google Sheets.

После разрешения конфликта выбранное значение применяется к обеим системам, а конфликт закрывается.

## Безопасность

- Административные API защищены HTTP Basic Authentication.
- Доступ к Google Sheets осуществляется через Service Account.
- Секретные данные хранятся в переменных окружения и отдельных файлах.
- `.env`, база данных и ключи доступа исключены из Git через `.gitignore`.

**Не публикуйте реальные токены, пароли и JSON-ключи в репозитории.**

## Тестирование

В проекте предусмотрены отдельные тестовые сценарии:

```bash
python test_sheets.py
python test_sync.py
python test_conflicts.py
```

Тесты охватывают работу с Google Sheets, синхронизацию данных и разрешение конфликтов.

## Статус проекта

Рабочий локальный прототип.

Основные функции приёма заявок, уведомлений, синхронизации и разрешения конфликтов реализованы и протестированы.

## Автор

Разработано как демонстрационный проект автоматизации бизнес-процессов.