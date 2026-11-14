
import asyncio
import logging

from app.services.sync_engine import sync_once


SYNC_INTERVAL = 30
sync_lock = asyncio.Lock()


async def sync_loop():
    """Запускает синхронизацию с интервалом 30 секунд."""

    logging.info(
        "Фоновая синхронизация запущена. Интервал: %s секунд",
        SYNC_INTERVAL,
    )

    while True:
        await asyncio.sleep(SYNC_INTERVAL)

        async with sync_lock:
            try:
                result = await asyncio.to_thread(sync_once)

                logging.info(
                    "Результат автоматической синхронизации: %s",
                    result,
                )

            except Exception:
                logging.exception(
                    "Ошибка фоновой синхронизации"
                )
