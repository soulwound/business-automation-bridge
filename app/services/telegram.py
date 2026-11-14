import os
import logging
import httpx

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

async def send_notification(lead_id, lead):
    if not BOT_TOKEN or not ADMIN_ID:
        logging.error("Telegram credentials are not configured")
        return False

    message = (
        f"Новая заявка №{lead_id}\n\n"
        f"Имя: {lead.name}\n"
        f"Телефон: {lead.phone}\n"
        f"Услуга: {lead.service}\n"
        f"Комментарий: {lead.comment or 'Не указан'}"
    )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                url,
                json={
                    "chat_id": ADMIN_ID,
                    "text": message
                }
            )

            response.raise_for_status()
            result = response.json()

            if not result.get("ok"):
                logging.error("Telegram API error: %s", result)
                return False

            return True


    except httpx.HTTPStatusError as error:

        logging.error(

            "Telegram HTTP error: %s",

            error.response.text

        )

        return False


    except httpx.HTTPError as error:

        logging.error(

            "Telegram connection error: %s",

            error

        )

        return False