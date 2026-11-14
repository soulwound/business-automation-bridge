
import logging
import os
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from app.database.db import get_lead, EDITABLE_FIELDS, utc_now


BASE_DIR = Path(__file__).resolve().parents[2]
CREDENTIALS_PATH = BASE_DIR / "credentials" / "service_account.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "ID",
    "Имя",
    "Телефон",
    "Услуга",
    "Комментарий",
    "Статус",
    "Дата создания",
    "Обновлено",
    "Sync Key",
]


def get_worksheet():
    """Подключается к Google Sheets и проверяет заголовки."""
    spreadsheet_id = os.getenv("SPREADSHEET_ID")

    if not spreadsheet_id:
        raise ValueError("SPREADSHEET_ID не указан в .env")

    credentials = Credentials.from_service_account_file(
        str(CREDENTIALS_PATH),
        scopes=SCOPES,
    )

    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(spreadsheet_id)
    worksheet = spreadsheet.sheet1

    # Добавляем технический столбец, если его ещё нет.
    current_headers = worksheet.row_values(1)

    if len(current_headers) < 9 or current_headers[8] != "Sync Key":
        worksheet.update_cell(1, 9, "Sync Key")

    return worksheet


def find_sheet_row(worksheet, lead_id):
    """Находит номер строки по ID заявки."""
    ids = worksheet.col_values(1)

    for row_number, value in enumerate(ids[1:], start=2):
        if value.strip() == str(lead_id):
            return row_number

    return None


def get_all_sheet_leads():
    """
    Возвращает заявки из Google Sheets.
    Включает строки без ID и сохраняет их реальные номера.
    """
    try:
        worksheet = get_worksheet()
        values = worksheet.get_all_values()

        if not values:
            return []

        headers = values[0]
        leads = []

        for row_number, row in enumerate(values[1:], start=2):
            record = {
                headers[index]: row[index] if index < len(row) else ""
                for index in range(len(headers))
            }

            # Пропускаем полностью пустые строки.
            if not any(str(value).strip() for value in record.values()):
                continue

            raw_id = record.get("ID", "").strip()

            if raw_id:
                try:
                    lead_id = int(raw_id)
                except (ValueError, TypeError):
                    logging.warning(
                        "Пропущена строка с некорректным ID: %s",
                        raw_id,
                    )
                    continue
            else:
                lead_id = None

            leads.append({
                "id": lead_id,
                "name": str(record.get("Имя", "")),
                "phone": str(record.get("Телефон", "")),
                "service": str(record.get("Услуга", "")),
                "comment": str(record.get("Комментарий", "")),
                "status": str(record.get("Статус", "Новая")) or "Новая",
                "created_at": str(record.get("Дата создания", "")),
                "updated_at": str(record.get("Обновлено", "")),
                "sync_key": str(record.get("Sync Key", "")).strip() or None,
                "_row_number": row_number,
            })

        return leads

    except Exception:
        logging.exception("Ошибка чтения Google Sheets")
        return None


def assign_sheet_row_metadata(row_number, lead_id=None, sync_key=None):
    """
    Записывает ID и Sync Key в указанную строку.
    Переданные значения обновляются одним запросом.
    """
    try:
        worksheet = get_worksheet()
        cells = []

        if lead_id is not None:
            cells.append(
                gspread.Cell(row_number, 1, lead_id)
            )

        if sync_key is not None:
            cells.append(
                gspread.Cell(row_number, 9, sync_key)
            )

        if cells:
            worksheet.update_cells(
                cells,
                value_input_option="USER_ENTERED",
            )

        return True

    except Exception:
        logging.exception(
            "Ошибка записи метаданных строки %s",
            row_number,
        )
        return False


def append_lead(lead_id, lead):
    """Добавляет заявку в таблицу, если её там ещё нет."""
    try:
        worksheet = get_worksheet()

        if find_sheet_row(worksheet, lead_id):
            return True

        saved_lead = get_lead(lead_id)

        if saved_lead is None:
            raise ValueError(
                f"Заявка {lead_id} не найдена в SQLite"
            )

        row = [
            saved_lead["id"],
            saved_lead["name"],
            saved_lead["phone"],
            saved_lead["service"],
            saved_lead["comment"],
            saved_lead["status"],
            saved_lead["created_at"],
            saved_lead["updated_at"],
            saved_lead.get("sync_key") or "",
        ]

        worksheet.append_row(
            row,
            value_input_option="USER_ENTERED",
        )

        return True

    except Exception:
        logging.exception(
            "Ошибка добавления заявки в Google Sheets"
        )
        return False


def update_sheet_lead(lead_id, fields):
    """Обновляет указанные поля заявки в Google Sheets."""
    try:
        invalid_fields = set(fields) - EDITABLE_FIELDS

        if invalid_fields:
            raise ValueError(
                f"Недопустимые поля: {invalid_fields}"
            )

        if not fields:
            return True

        worksheet = get_worksheet()
        row_number = find_sheet_row(worksheet, lead_id)

        if row_number is None:
            return False

        column_mapping = {
            "name": 2,
            "phone": 3,
            "service": 4,
            "comment": 5,
            "status": 6,
        }

        cells = []

        for field, value in fields.items():
            column = column_mapping[field]
            cells.append(
                gspread.Cell(row_number, column, value)
            )

        worksheet.update_cells(
            cells,
            value_input_option="USER_ENTERED",
        )

        worksheet.update_cell(
            row_number,
            8,
            utc_now(),
        )

        return True

    except Exception:
        logging.exception(
            "Ошибка обновления заявки в Google Sheets"
        )
        return False

def update_sheet_conflict_value(lead_id, field_name, value):
    """
    Обновляет одно поле заявки в Google Sheets
    при разрешении конфликта.
    """
    column_mapping = {
        "name": 2,
        "phone": 3,
        "service": 4,
        "comment": 5,
        "status": 6,
    }

    if field_name not in column_mapping:
        raise ValueError(
            f"Недопустимое поле: {field_name}"
        )

    worksheet = get_worksheet()
    row_number = find_sheet_row(worksheet, lead_id)

    if row_number is None:
        raise ValueError(
            f"Заявка {lead_id} не найдена в Google Sheets"
        )

    worksheet.update_cell(
        row_number,
        column_mapping[field_name],
        value,
    )

    worksheet.update_cell(
        row_number,
        8,
        utc_now(),
    )

    return True
