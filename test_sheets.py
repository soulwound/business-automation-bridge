
from dotenv import load_dotenv

load_dotenv()

from app.services.google_sheets import update_sheet_lead

# Временно меняем комментарий заявки №7.
success = update_sheet_lead(
    7,
    {"comment": "ТЕСТ СИНХРОНИЗАЦИИ"}
)

if success:
    print("Успешно! Строка обновлена.")
    print("Проверь заявку №7 в Google Sheets.")
else:
    print("Не удалось обновить строку.")
