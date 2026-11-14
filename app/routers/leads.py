import asyncio
import json
from typing import Literal
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.auth import verify_admin
from app.database.db import (
    save_lead,
    get_all_leads,
    get_sync_logs,
    get_open_conflicts,
    update_status,
    EDITABLE_FIELDS
)
from app.services.telegram import send_notification
from app.services.google_sheets import append_lead
from app.database.db import (
    get_sync_conflict,
    apply_sync_conflict_resolution,
    log_sync_event,
)

from app.services.google_sheets import update_sheet_conflict_value
from app.services.sync_scheduler import sync_lock

router = APIRouter(prefix="/api", tags=["Leads"])


class Lead(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=100)
    phone: str = Field(min_length=5, max_length=30)
    service: str = Field(min_length=2, max_length=100)
    comment: str = Field(default="", max_length=1000)


class StatusUpdate(BaseModel):
    status: Literal["Новая", "В работе", "Завершена"]

class ConflictResolution(BaseModel):
    source: Literal["sqlite", "sheets"]

def decode_json_value(value):
    """Преобразует JSON-строку в Python-значение."""
    if not isinstance(value, str):
        return value

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


@router.post("/leads")
async def create_lead(lead: Lead):
    lead_id = save_lead(lead)

    notification_sent = await send_notification(lead_id, lead)

    sheets_sent = await asyncio.to_thread(
        append_lead,
        lead_id,
        lead,
    )

    return {
        "success": True,
        "message": "Заявка успешно сохранена!",
        "lead_id": lead_id,
        "notification_sent": notification_sent,
        "sheets_sent": sheets_sent,
    }


@router.get(
    "/leads",
    dependencies=[Depends(verify_admin)],
)
async def list_leads():
    return get_all_leads()


@router.patch(
    "/leads/{lead_id}/status",
    dependencies=[Depends(verify_admin)],
)
async def update_lead_status(lead_id: int, data: StatusUpdate):
    updated = update_status(lead_id, data.status)

    if not updated:
        raise HTTPException(
            status_code=404,
            detail="Заявка не найдена",
        )

    return {
        "success": True,
        "lead_id": lead_id,
        "status": data.status,
    }


@router.get(
    "/sync-logs",
    dependencies=[Depends(verify_admin)],
)
async def list_sync_logs(
    limit: int = Query(default=100, ge=1, le=500),
):
    return get_sync_logs(limit)


@router.get(
    "/sync-conflicts",
    dependencies=[Depends(verify_admin)],
)
async def list_sync_conflicts():
    conflicts = get_open_conflicts()

    for conflict in conflicts:
        conflict["sqlite_value"] = decode_json_value(
            conflict.get("sqlite_value")
        )
        conflict["sheets_value"] = decode_json_value(
            conflict.get("sheets_value")
        )

    return conflicts

def decode_conflict_value(value):
    """Преобразует сохранённое JSON-значение в Python-объект."""
    if not isinstance(value, str):
        return value

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def resolve_conflict_sync(conflict_id, source):
    """
    Синхронно разрешает конфликт:
    1. Получает конфликт.
    2. Обновляет Google Sheets.
    3. Обновляет SQLite и baseline.
    """
    conflict = get_sync_conflict(conflict_id)

    if conflict is None:
        raise HTTPException(
            status_code=404,
            detail="Конфликт не найден",
        )

    if conflict["resolved"]:
        raise HTTPException(
            status_code=409,
            detail="Этот конфликт уже разрешён",
        )

    lead_id = conflict["lead_id"]
    field_name = conflict["field_name"]

    if field_name not in EDITABLE_FIELDS:
        raise HTTPException(
            status_code=400,
            detail="Недопустимое поле конфликта",
        )

    if source == "sqlite":
        value = decode_conflict_value(
            conflict["sqlite_value"]
        )
    else:
        value = decode_conflict_value(
            conflict["sheets_value"]
        )

    # Сначала обновляем Google Sheets.
    update_sheet_conflict_value(
        lead_id,
        field_name,
        value,
    )

    # Только после успешной записи обновляем SQLite и baseline.
    applied = apply_sync_conflict_resolution(
        conflict_id,
        value,
    )

    if not applied:
        raise HTTPException(
            status_code=409,
            detail=(
                "Google Sheets обновлена, "
                "но не удалось завершить разрешение конфликта в SQLite. "
                "Конфликт оставлен открытым."
            ),
        )

    try:
        log_sync_event(
            level="INFO",
            action="RESOLVE_CONFLICT",
            message=(
                f"Конфликт поля '{field_name}' разрешён. "
                f"Выбран источник: {source}"
            ),
            lead_id=lead_id,
        )
    except Exception:
        logging.exception(
            "Не удалось записать результат разрешения конфликта"
        )

    return {
        "success": True,
        "conflict_id": conflict_id,
        "lead_id": lead_id,
        "field": field_name,
        "source": source,
        "message": "Конфликт успешно разрешён",
    }


@router.post(
    "/sync-conflicts/{conflict_id}/resolve",
    dependencies=[Depends(verify_admin)],
)
async def resolve_sync_conflict(
    conflict_id: int,
    data: ConflictResolution,
):
    async with sync_lock:
        try:
            return await asyncio.to_thread(
                resolve_conflict_sync,
                conflict_id,
                data.source,
            )

        except HTTPException:
            raise

        except Exception:
            logging.exception(
                "Ошибка разрешения конфликта ID=%s",
                conflict_id,
            )

            raise HTTPException(
                status_code=502,
                detail=(
                    "Не удалось разрешить конфликт. "
                    "Проверьте доступность Google Sheets и SQLite. "
                    "Если Google Sheets уже обновилась, конфликт "
                    "можно попробовать разрешить повторно."
                ),
            )