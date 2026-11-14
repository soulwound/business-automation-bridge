
from dotenv import load_dotenv

load_dotenv()

from app.services.sync_engine import sync_once

result = sync_once()

print("Результат синхронизации:")
print(result)
