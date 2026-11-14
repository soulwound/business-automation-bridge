
import tempfile
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.database.db as db
import app.routers.leads as leads_router
from app.auth import verify_admin
from app.main import app


def fake_update_sheet(lead_id, field_name, value):
    """Заглушка вместо реальной записи в Google Sheets."""
    sheet_calls.append({
        "lead_id": lead_id,
        "field_name": field_name,
        "value": value,
    })
    return True


sheet_calls = []


def create_test_lead(name):
    lead = SimpleNamespace(
        name=name,
        phone="+79990000000",
        service="Тестовая услуга",
        comment="Исходный комментарий",
    )
    return db.save_lead(lead)


def main():
    original_db_path = db.DB_PATH
    original_sheet_function = leads_router.update_sheet_conflict_value
    original_overrides = app.dependency_overrides.copy()

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            # Используем отдельную временную базу.
            db.DB_PATH = Path(temp_dir) / "test_leads.db"
            db.init_db()

            # Подменяем запись в настоящую Google Sheets.
            leads_router.update_sheet_conflict_value = fake_update_sheet

            # Отключаем проверку авторизации только для теста.
            app.dependency_overrides[verify_admin] = lambda: True

            # Создаём две тестовые заявки.
            lead_1 = create_test_lead("Тест SQLite")
            lead_2 = create_test_lead("Тест Sheets")

            # Создаём два тестовых конфликта.
            db.record_sync_conflict(
                lead_1,
                "comment",
                "Комментарий из SQLite",
                "Комментарий из Sheets",
            )

            db.record_sync_conflict(
                lead_2,
                "comment",
                "Вариант SQLite",
                "Вариант Sheets",
            )

            conflicts = db.get_open_conflicts()

            assert len(conflicts) == 2, (
                f"Ожидалось 2 конфликта, получено: {len(conflicts)}"
            )

            conflict_ids = {
                conflict["lead_id"]: conflict["id"]
                for conflict in conflicts
            }

            with TestClient(app) as client:
                # Проверяем получение конфликтов через API.
                response = client.get("/api/sync-conflicts")

                assert response.status_code == 200
                assert len(response.json()) == 2

                print("GET /api/sync-conflicts: OK")

                # Сценарий 1: оставить значение SQLite.
                response = client.post(
                    f"/api/sync-conflicts/{conflict_ids[lead_1]}/resolve",
                    json={"source": "sqlite"},
                )

                assert response.status_code == 200, response.text
                assert response.json()["success"] is True

                updated_lead = db.get_lead(lead_1)

                assert updated_lead["comment"] == "Комментарий из SQLite"

                print("Разрешение конфликта в пользу SQLite: OK")

                # Сценарий 2: оставить значение Google Sheets.
                response = client.post(
                    f"/api/sync-conflicts/{conflict_ids[lead_2]}/resolve",
                    json={"source": "sheets"},
                )

                assert response.status_code == 200, response.text
                assert response.json()["success"] is True

                updated_lead = db.get_lead(lead_2)

                assert updated_lead["comment"] == "Вариант Sheets"

                print("Разрешение конфликта в пользу Google Sheets: OK")

                # Проверяем, что оба конфликта закрыты.
                remaining = client.get("/api/sync-conflicts")

                assert remaining.status_code == 200
                assert remaining.json() == []

                print("Закрытие конфликтов: OK")

                # Проверяем, что заглушка вызывалась дважды.
                assert len(sheet_calls) == 2

                print("Имитация обновления Google Sheets: OK")

                # Проверяем повторное разрешение.
                response = client.post(
                    f"/api/sync-conflicts/{conflict_ids[lead_1]}/resolve",
                    json={"source": "sqlite"},
                )

                assert response.status_code == 409

                print("Защита от повторного разрешения: OK")

                # Проверяем журнал.
                logs = client.get("/api/sync-logs")

                assert logs.status_code == 200
                assert len(logs.json()) == 2

                print("Запись событий в журнал: OK")

            print("\nВсе тесты успешно пройдены.")

    finally:
        db.DB_PATH = original_db_path
        leads_router.update_sheet_conflict_value = original_sheet_function
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)


if __name__ == "__main__":
    main()
