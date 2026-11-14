
from dotenv import load_dotenv

load_dotenv()

from app.database.db import (
    get_open_conflicts,
    update_lead_fields,
    get_lead,
    save_sync_state,
    resolve_sync_conflict,
)

from app.services.google_sheets import update_sheet_lead
from app.services.sync_engine import make_snapshot


# Выбранные значения для каждого конфликта.
# Ключ: ID заявки, значение: словарь полей.
RESOLUTIONS = {
    9: {
        "status": "Завершена",
    },
    8: {
        "status": "В работе",
    },
    7: {
        "comment": "ТЕСТ СИНХРОНИЗАЦИИ",
        "status": "Завершена",
    },
}


def main():
    conflicts = get_open_conflicts()

    if not conflicts:
        print("Открытых конфликтов нет.")
        return

    grouped = {}

    for conflict in conflicts:
        lead_id = conflict["lead_id"]
        field = conflict["field_name"]

        if lead_id not in RESOLUTIONS:
            continue

        if field not in RESOLUTIONS[lead_id]:
            continue

        grouped.setdefault(lead_id, {})[field] = (
            RESOLUTIONS[lead_id][field]
        )

    for lead_id, fields in grouped.items():
        print(f"\nОбрабатываем заявку №{lead_id}")

        # Сначала обновляем SQLite.
        if not update_lead_fields(lead_id, fields):
            print("Ошибка обновления SQLite.")
            continue

        # Затем Google Sheets.
        if not update_sheet_lead(lead_id, fields):
            print("Ошибка обновления Google Sheets.")
            print("Конфликты оставлены открытыми.")
            continue

        # Получаем итоговые данные из SQLite.
        lead = get_lead(lead_id)

        if lead is None:
            print("Заявка не найдена после обновления.")
            continue

        # Сохраняем новую общую версию.
        save_sync_state(lead_id, make_snapshot(lead))

        # Закрываем соответствующие конфликты.
        for conflict in conflicts:
            if conflict["lead_id"] != lead_id:
                continue

            field = conflict["field_name"]

            if field not in fields:
                continue

            resolved = resolve_sync_conflict(
                conflict["id"],
                fields[field],
            )

            if resolved:
                print(f"Конфликт '{field}' разрешён.")

        print("Заявка синхронизирована.")


if __name__ == "__main__":
    main()
