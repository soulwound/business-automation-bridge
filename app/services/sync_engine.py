
import logging
import uuid

from app.database.db import (
    EDITABLE_FIELDS,
    get_all_leads,
    get_sync_state,
    save_sync_state,
    record_sync_conflict,
    update_lead_fields,
    import_sheet_lead,
    import_sheet_lead_without_id,
    get_lead_by_sync_key,
    log_sync_event,
    get_open_conflicts,
)

from app.services.google_sheets import (
    get_all_sheet_leads,
    append_lead,
    update_sheet_lead,
    assign_sheet_row_metadata,
)


def _log_event(level, action, message, lead_id=None):
    """Записывает событие, не прерывая синхронизацию при ошибке журнала."""
    try:
        log_sync_event(
            level=level,
            action=action,
            message=message,
            lead_id=lead_id,
        )
    except Exception:
        logging.exception("Не удалось записать событие в журнал")


def make_snapshot(lead):
    """Создаёт снимок редактируемых полей заявки."""
    return {
        field: str(lead.get(field) or "")
        for field in EDITABLE_FIELDS
    }


def prepare_sheet_rows(sheet_data, result):
    """
    Обрабатывает строки Google Sheets без ID:
    назначает Sync Key, импортирует в SQLite и записывает ID обратно.
    """
    for remote in sheet_data:
        if remote["id"] is not None:
            continue

        row_number = remote.get("_row_number")

        try:
            sync_key = remote.get("sync_key")

            if not sync_key:
                sync_key = str(uuid.uuid4())

                if not assign_sheet_row_metadata(
                    row_number,
                    sync_key=sync_key,
                ):
                    result["errors"] += 1
                    _log_event(
                        "ERROR",
                        "ASSIGN_SYNC_KEY",
                        f"Не удалось записать Sync Key в строку {row_number}",
                    )
                    continue

                remote["sync_key"] = sync_key

            existing = get_lead_by_sync_key(sync_key)

            if existing:
                lead_id = existing["id"]
            else:
                lead_id = import_sheet_lead_without_id(
                    remote,
                    sync_key,
                )
                result["sheets_to_sqlite"] += 1

                _log_event(
                    "INFO",
                    "IMPORT_FROM_SHEETS",
                    f"Заявка импортирована из строки Google Sheets №{row_number}",
                    lead_id,
                )

            if not assign_sheet_row_metadata(
                row_number,
                lead_id=lead_id,
            ):
                result["errors"] += 1
                _log_event(
                    "ERROR",
                    "ASSIGN_LEAD_ID",
                    f"Не удалось записать ID в строку Google Sheets №{row_number}",
                    lead_id,
                )
                continue

            remote["id"] = lead_id

        except Exception as error:
            logging.exception(
                "Ошибка импорта строки Google Sheets: %s",
                row_number,
            )
            result["errors"] += 1

            _log_event(
                "ERROR",
                "IMPORT_FROM_SHEETS",
                f"Ошибка обработки строки {row_number}: {error}",
            )

    return sheet_data


def sync_once():
    """Выполняет один цикл двусторонней синхронизации."""

    result = {
        "success": False,
        "sqlite_to_sheets": 0,
        "sheets_to_sqlite": 0,
        "updated_sqlite": 0,
        "updated_sheets": 0,
        "conflicts": 0,
        "errors": 0,
    }

    _log_event(
        "INFO",
        "SYNC_STARTED",
        "Начат цикл двусторонней синхронизации",
    )

    try:
        sqlite_leads = {
            lead["id"]: lead
            for lead in get_all_leads()
        }

        sheet_data = get_all_sheet_leads()

        if sheet_data is None:
            logging.error(
                "Не удалось получить данные Google Sheets"
            )

            result["errors"] += 1

            _log_event(
                "ERROR",
                "READ_GOOGLE_SHEETS",
                "Не удалось получить данные Google Sheets",
            )

            return result

        sheet_data = prepare_sheet_rows(sheet_data, result)

        open_conflict_fields = {
            (conflict["lead_id"], conflict["field_name"])
            for conflict in get_open_conflicts()
        }

        result["conflicts"] = len(open_conflict_fields)

        sqlite_leads = {
            lead["id"]: lead
            for lead in get_all_leads()
        }

        sheets_leads = {
            lead["id"]: lead
            for lead in sheet_data
            if lead["id"] is not None
        }

        all_ids = set(sqlite_leads) | set(sheets_leads)

        for lead_id in all_ids:
            try:
                local = sqlite_leads.get(lead_id)
                remote = sheets_leads.get(lead_id)

                # Проверяем, есть ли у заявки открытые конфликты.
                lead_has_open_conflicts = any(
                    conflict_lead_id == lead_id
                    for conflict_lead_id, _ in open_conflict_fields
                )

                # Если одна из сторон отсутствует, не переносим
                # всю заявку при наличии открытых конфликтов.
                if lead_has_open_conflicts and (
                    local is None or remote is None
                ):
                    continue

                # Заявка есть только в SQLite.
                if local and not remote:
                    if append_lead(lead_id, None):
                        save_sync_state(
                            lead_id,
                            make_snapshot(local),
                        )

                        result["sqlite_to_sheets"] += 1

                        _log_event(
                            "INFO",
                            "EXPORT_TO_SHEETS",
                            "Заявка добавлена в Google Sheets",
                            lead_id,
                        )
                    else:
                        result["errors"] += 1

                        _log_event(
                            "ERROR",
                            "EXPORT_TO_SHEETS",
                            "Не удалось добавить заявку в Google Sheets",
                            lead_id,
                        )

                    continue

                # Заявка есть только в Google Sheets.
                if remote and not local:
                    if import_sheet_lead(remote):
                        save_sync_state(
                            lead_id,
                            make_snapshot(remote),
                        )

                        result["sheets_to_sqlite"] += 1

                        _log_event(
                            "INFO",
                            "IMPORT_FROM_SHEETS",
                            "Заявка импортирована в SQLite",
                            lead_id,
                        )
                    else:
                        result["errors"] += 1

                        _log_event(
                            "ERROR",
                            "IMPORT_FROM_SHEETS",
                            "Не удалось импортировать заявку в SQLite",
                            lead_id,
                        )

                    continue

                # Заявка существует в обеих системах.
                local_data = make_snapshot(local)
                remote_data = make_snapshot(remote)

                state = get_sync_state(lead_id)

                # Для старых заявок ещё нет общей версии.
                if state is None:
                    differences = {
                        field
                        for field in EDITABLE_FIELDS
                        if (
                            local_data[field] != remote_data[field]
                            and (lead_id, field) not in open_conflict_fields
                        )
                    }

                    if differences:
                        for field in differences:
                            is_new = record_sync_conflict(
                                lead_id,
                                field,
                                local_data[field],
                                remote_data[field],
                            )

                            if is_new:
                                _log_event(
                                    "WARNING",
                                    "SYNC_CONFLICT",
                                    f"Обнаружен конфликт поля '{field}'",
                                    lead_id,
                                )

                        result["conflicts"] += len(differences)

                    else:
                        has_pending_conflicts = any(
                            conflict_lead_id == lead_id
                            for conflict_lead_id, _ in open_conflict_fields
                        )

                        if not has_pending_conflicts:
                            save_sync_state(lead_id, local_data)

                    continue

                baseline = state["data"]

                # Исключаем поля с открытыми конфликтами.
                local_changed = {
                    field
                    for field in EDITABLE_FIELDS
                    if (
                        local_data[field] != str(
                            baseline.get(field, "") or ""
                        )
                        and (lead_id, field) not in open_conflict_fields
                    )
                }

                remote_changed = {
                    field
                    for field in EDITABLE_FIELDS
                    if (
                        remote_data[field] != str(
                            baseline.get(field, "") or ""
                        )
                        and (lead_id, field) not in open_conflict_fields
                    )
                }

                common_changes = local_changed & remote_changed

                conflicts = {
                    field
                    for field in common_changes
                    if local_data[field] != remote_data[field]
                }

                for field in conflicts:
                    is_new = record_sync_conflict(
                        lead_id,
                        field,
                        local_data[field],
                        remote_data[field],
                    )

                    if is_new:
                        _log_event(
                            "WARNING",
                            "SYNC_CONFLICT",
                            f"Обнаружен конфликт поля '{field}'",
                            lead_id,
                        )

                result["conflicts"] += len(conflicts)

                sheets_only = remote_changed - local_changed
                sqlite_only = local_changed - remote_changed

                sheets_only -= conflicts
                sqlite_only -= conflicts

                operations_ok = True

                # Обновляем SQLite.
                if sheets_only:
                    fields = {
                        field: remote_data[field]
                        for field in sheets_only
                    }

                    if update_lead_fields(lead_id, fields):
                        result["updated_sqlite"] += 1

                        _log_event(
                            "INFO",
                            "UPDATE_SQLITE",
                            f"Обновлены поля: {', '.join(sorted(fields))}",
                            lead_id,
                        )
                    else:
                        result["errors"] += 1
                        operations_ok = False

                        _log_event(
                            "ERROR",
                            "UPDATE_SQLITE",
                            "Не удалось обновить заявку в SQLite",
                            lead_id,
                        )

                # Обновляем Google Sheets.
                if sqlite_only:
                    fields = {
                        field: local_data[field]
                        for field in sqlite_only
                    }

                    if update_sheet_lead(lead_id, fields):
                        result["updated_sheets"] += 1

                        _log_event(
                            "INFO",
                            "UPDATE_SHEETS",
                            f"Обновлены поля: {', '.join(sorted(fields))}",
                            lead_id,
                        )
                    else:
                        result["errors"] += 1
                        operations_ok = False

                        _log_event(
                            "ERROR",
                            "UPDATE_SHEETS",
                            "Не удалось обновить заявку в Google Sheets",
                            lead_id,
                        )

                # Не обновляем baseline при новых конфликтах или ошибках.
                if conflicts or not operations_ok:
                    continue

                merged = dict(local_data)

                for field in sheets_only:
                    merged[field] = remote_data[field]

                # Сохраняем прежний baseline для открытых конфликтов.
                for field in EDITABLE_FIELDS:
                    if (lead_id, field) in open_conflict_fields:
                        merged[field] = str(
                            baseline.get(field, "") or ""
                        )

                save_sync_state(lead_id, merged)

            except Exception as error:
                logging.exception(
                    "Ошибка синхронизации заявки ID=%s",
                    lead_id,
                )

                result["errors"] += 1

                _log_event(
                    "ERROR",
                    "SYNC_LEAD",
                    f"Ошибка синхронизации заявки: {error}",
                    lead_id,
                )

        result["success"] = result["errors"] == 0

        _log_event(
            "INFO" if result["success"] else "WARNING",
            "SYNC_FINISHED",
            (
                f"Цикл завершён. "
                f"SQLite → Sheets: {result['sqlite_to_sheets']}; "
                f"Sheets → SQLite: {result['sheets_to_sqlite']}; "
                f"Обновлено SQLite: {result['updated_sqlite']}; "
                f"Обновлено Sheets: {result['updated_sheets']}; "
                f"Конфликтов: {result['conflicts']}; "
                f"Ошибок: {result['errors']}"
            ),
        )

        return result

    except Exception as error:
        logging.exception("Ошибка цикла синхронизации")

        result["errors"] += 1

        _log_event(
            "ERROR",
            "SYNC_FAILED",
            f"Критическая ошибка цикла синхронизации: {error}",
        )

        return result
