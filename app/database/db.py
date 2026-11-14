
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent / "leads.db"

EDITABLE_FIELDS = {
    "name",
    "phone",
    "service",
    "comment",
    "status",
}


def utc_now():
    """Текущее время в UTC в формате ISO 8601."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    """Создание таблиц и безопасное обновление существующей базы."""
    connection = get_connection()

    try:
        with connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    service TEXT NOT NULL,
                    comment TEXT DEFAULT '',
                    status TEXT DEFAULT 'Новая',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT
                )
            """)

            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(leads)"
                ).fetchall()
            }

            if "updated_at" not in columns:
                connection.execute(
                    "ALTER TABLE leads ADD COLUMN updated_at TEXT"
                )

            # Технический ключ для безопасного импорта из Google Sheets.
            if "sync_key" not in columns:
                connection.execute(
                    "ALTER TABLE leads ADD COLUMN sync_key TEXT"
                )

            connection.execute("""
                UPDATE leads
                SET updated_at = COALESCE(updated_at, created_at, ?)
                WHERE updated_at IS NULL
            """, (utc_now(),))

            # Один ключ может принадлежать только одной заявке.
            connection.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_sync_key
                ON leads(sync_key)
                WHERE sync_key IS NOT NULL
            """)

            connection.execute("""
                CREATE TABLE IF NOT EXISTS sync_state (
                    lead_id INTEGER PRIMARY KEY,
                    last_synced_data TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                )
            """)

            connection.execute("""
                CREATE TABLE IF NOT EXISTS sync_conflicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER NOT NULL,
                    field_name TEXT NOT NULL,
                    sqlite_value TEXT,
                    sheets_value TEXT,
                    detected_at TEXT NOT NULL,
                    resolved INTEGER NOT NULL DEFAULT 0,
                    resolved_value TEXT
                )
            """)

            connection.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_conflict_per_field
                ON sync_conflicts(lead_id, field_name)
                WHERE resolved = 0
            """)
            # Журнал событий синхронизации.
            connection.execute("""
                CREATE TABLE IF NOT EXISTS sync_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL DEFAULT 'INFO',
                    action TEXT NOT NULL,
                    lead_id INTEGER,
                    message TEXT NOT NULL
                )
            """)

            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_sync_logs_created_at
                ON sync_logs(created_at)
            """)

    finally:
        connection.close()


def save_lead(lead):
    """Сохраняет новую заявку и возвращает её ID."""
    now = utc_now()
    connection = get_connection()

    try:
        with connection:
            cursor = connection.execute("""
                INSERT INTO leads (
                    name, phone, service, comment, status,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                lead.name,
                lead.phone,
                lead.service,
                lead.comment,
                "Новая",
                now,
                now,
            ))

            return cursor.lastrowid

    finally:
        connection.close()


def get_all_leads():
    """Возвращает все заявки, начиная с последних."""
    connection = get_connection()

    try:
        rows = connection.execute("""
            SELECT *
            FROM leads
            ORDER BY id DESC
        """).fetchall()

        return [dict(row) for row in rows]

    finally:
        connection.close()


def get_lead(lead_id):
    """Возвращает одну заявку по ID."""
    connection = get_connection()

    try:
        row = connection.execute(
            "SELECT * FROM leads WHERE id = ?",
            (lead_id,)
        ).fetchone()

        return dict(row) if row else None

    finally:
        connection.close()


def get_lead_by_sync_key(sync_key):
    """Находит заявку по техническому ключу синхронизации."""
    connection = get_connection()

    try:
        row = connection.execute(
            "SELECT * FROM leads WHERE sync_key = ?",
            (sync_key,)
        ).fetchone()

        return dict(row) if row else None

    finally:
        connection.close()


def update_lead_fields(lead_id, fields):
    """Обновляет разрешённые поля заявки."""
    if not fields:
        return get_lead(lead_id) is not None

    invalid_fields = set(fields) - EDITABLE_FIELDS

    if invalid_fields:
        raise ValueError(
            f"Недопустимые поля для обновления: {invalid_fields}"
        )

    assignments = ", ".join(
        f"{field} = ?" for field in fields
    )

    values = list(fields.values())
    values.append(utc_now())
    values.append(lead_id)

    connection = get_connection()

    try:
        with connection:
            cursor = connection.execute(
                f"""
                UPDATE leads
                SET {assignments}, updated_at = ?
                WHERE id = ?
                """,
                values
            )

            return cursor.rowcount > 0

    finally:
        connection.close()


def update_status(lead_id, status):
    """Обновляет статус заявки."""
    return update_lead_fields(lead_id, {"status": status})


def get_sync_state(lead_id):
    """Получает последнюю синхронизированную версию заявки."""
    connection = get_connection()

    try:
        row = connection.execute("""
            SELECT last_synced_data, synced_at
            FROM sync_state
            WHERE lead_id = ?
        """, (lead_id,)).fetchone()

        if row is None:
            return None

        return {
            "data": json.loads(row["last_synced_data"]),
            "synced_at": row["synced_at"],
        }

    finally:
        connection.close()


def save_sync_state(lead_id, data):
    """Сохраняет общую версию данных после синхронизации."""
    connection = get_connection()

    try:
        with connection:
            connection.execute("""
                INSERT INTO sync_state (
                    lead_id, last_synced_data, synced_at
                )
                VALUES (?, ?, ?)
                ON CONFLICT(lead_id) DO UPDATE SET
                    last_synced_data = excluded.last_synced_data,
                    synced_at = excluded.synced_at
            """, (
                lead_id,
                json.dumps(data, ensure_ascii=False),
                utc_now(),
            ))

    finally:
        connection.close()


def record_sync_conflict(
    lead_id,
    field_name,
    sqlite_value,
    sheets_value
):
    """Записывает конфликт, если для поля ещё нет открытого."""
    connection = get_connection()

    try:
        with connection:
            cursor = connection.execute("""
                INSERT OR IGNORE INTO sync_conflicts (
                    lead_id, field_name, sqlite_value,
                    sheets_value, detected_at
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                lead_id,
                field_name,
                json.dumps(sqlite_value, ensure_ascii=False),
                json.dumps(sheets_value, ensure_ascii=False),
                utc_now(),
            ))

            return cursor.rowcount > 0

    finally:
        connection.close()


def get_open_conflicts():
    """Возвращает все неразрешённые конфликты."""
    connection = get_connection()

    try:
        rows = connection.execute("""
            SELECT *
            FROM sync_conflicts
            WHERE resolved = 0
            ORDER BY detected_at DESC
        """).fetchall()

        return [dict(row) for row in rows]

    finally:
        connection.close()


def import_sheet_lead(lead):
    """Импортирует заявку из Google Sheets с уже заданным ID."""
    connection = get_connection()

    try:
        with connection:
            existing = connection.execute(
                "SELECT id FROM leads WHERE id = ?",
                (lead["id"],)
            ).fetchone()

            if existing:
                return False

            connection.execute("""
                INSERT INTO leads (
                    id, name, phone, service, comment,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                lead["id"],
                lead["name"],
                lead["phone"],
                lead["service"],
                lead["comment"],
                lead["status"],
                lead["created_at"] or utc_now(),
                lead["updated_at"] or utc_now(),
            ))

            return True

    finally:
        connection.close()


def import_sheet_lead_without_id(lead, sync_key):
    """
    Импортирует строку без ID.
    Повторный вызов с тем же sync_key не создаёт дубликат.
    Возвращает ID заявки.
    """
    if not sync_key:
        raise ValueError("Для импорта необходим sync_key")

    connection = get_connection()

    try:
        with connection:
            existing = connection.execute(
                "SELECT id FROM leads WHERE sync_key = ?",
                (sync_key,)
            ).fetchone()

            if existing:
                return existing["id"]

            cursor = connection.execute("""
                INSERT INTO leads (
                    name, phone, service, comment,
                    status, created_at, updated_at, sync_key
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                lead["name"],
                lead["phone"],
                lead["service"],
                lead["comment"],
                lead["status"],
                lead.get("created_at") or utc_now(),
                lead.get("updated_at") or utc_now(),
                sync_key,
            ))

            return cursor.lastrowid

    finally:
        connection.close()


def resolve_sync_conflict(conflict_id, resolved_value):
    """Закрывает конфликт после успешной синхронизации."""
    connection = get_connection()

    try:
        with connection:
            cursor = connection.execute("""
                UPDATE sync_conflicts
                SET resolved = 1,
                    resolved_value = ?
                WHERE id = ?
                  AND resolved = 0
            """, (
                json.dumps(resolved_value, ensure_ascii=False),
                conflict_id,
            ))

            return cursor.rowcount > 0

    finally:
        connection.close()

def log_sync_event(level, action, message, lead_id=None):
    """Записывает событие в журнал синхронизации."""
    allowed_levels = {"INFO", "WARNING", "ERROR"}

    level = level.upper()

    if level not in allowed_levels:
        raise ValueError(f"Недопустимый уровень журнала: {level}")

    connection = get_connection()

    try:
        with connection:
            cursor = connection.execute("""
                INSERT INTO sync_logs (
                    created_at, level, action, lead_id, message
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                utc_now(),
                level,
                action,
                lead_id,
                message,
            ))

            return cursor.lastrowid

    finally:
        connection.close()


def get_sync_logs(limit=100):
    """Возвращает последние события журнала."""
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("limit должен быть положительным целым числом")

    connection = get_connection()

    try:
        rows = connection.execute("""
            SELECT *
            FROM sync_logs
            ORDER BY id DESC
            LIMIT ?
        """, (limit,)).fetchall()

        return [dict(row) for row in rows]

    finally:
        connection.close()

def get_sync_conflict(conflict_id):
    """Возвращает конфликт по ID."""
    connection = get_connection()

    try:
        row = connection.execute("""
            SELECT *
            FROM sync_conflicts
            WHERE id = ?
        """, (conflict_id,)).fetchone()

        return dict(row) if row else None

    finally:
        connection.close()


def apply_sync_conflict_resolution(conflict_id, resolved_value):
    """
    Атомарно применяет решение в SQLite:
    1. Обновляет поле заявки.
    2. Обновляет baseline синхронизации.
    3. Закрывает конфликт.

    Google Sheets должна быть обновлена до вызова этой функции.
    """
    connection = get_connection()

    try:
        with connection:
            conflict = connection.execute("""
                SELECT *
                FROM sync_conflicts
                WHERE id = ?
                  AND resolved = 0
            """, (conflict_id,)).fetchone()

            if conflict is None:
                return False

            lead_id = conflict["lead_id"]
            field_name = conflict["field_name"]

            if field_name not in EDITABLE_FIELDS:
                raise ValueError(
                    f"Недопустимое поле конфликта: {field_name}"
                )

            cursor = connection.execute(
                f"""
                UPDATE leads
                SET {field_name} = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (resolved_value, utc_now(), lead_id),
            )

            if cursor.rowcount == 0:
                raise ValueError(
                    f"Заявка {lead_id} не найдена"
                )

            lead = connection.execute("""
                SELECT name, phone, service, comment, status
                FROM leads
                WHERE id = ?
            """, (lead_id,)).fetchone()

            snapshot = {
                field: str(lead[field] or "")
                for field in EDITABLE_FIELDS
            }

            connection.execute("""
                INSERT INTO sync_state (
                    lead_id,
                    last_synced_data,
                    synced_at
                )
                VALUES (?, ?, ?)
                ON CONFLICT(lead_id) DO UPDATE SET
                    last_synced_data = excluded.last_synced_data,
                    synced_at = excluded.synced_at
            """, (
                lead_id,
                json.dumps(snapshot, ensure_ascii=False),
                utc_now(),
            ))

            cursor = connection.execute("""
                UPDATE sync_conflicts
                SET resolved = 1,
                    resolved_value = ?
                WHERE id = ?
                  AND resolved = 0
            """, (
                json.dumps(resolved_value, ensure_ascii=False),
                conflict_id,
            ))

            if cursor.rowcount == 0:
                raise RuntimeError(
                    "Не удалось закрыть конфликт"
                )

            return True

    finally:
        connection.close()
