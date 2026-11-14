
import asyncio
import logging

from contextlib import asynccontextmanager, suppress
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Depends
from fastapi.responses import FileResponse

from app.routers.leads import router as leads_router
from app.database.db import init_db
from app.auth import verify_admin
from app.services.sync_scheduler import sync_loop


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    sync_task = asyncio.create_task(sync_loop())

    logging.info("Business Automation Bridge запущен")

    try:
        yield
    finally:
        sync_task.cancel()

        with suppress(asyncio.CancelledError):
            await sync_task

        logging.info("Фоновая синхронизация остановлена")


app = FastAPI(
    title="Business Automation Bridge",
    description="Система автоматизации обработки заявок",
    version="1.0.0",
    lifespan=lifespan,
)

BASE_DIR = Path(__file__).resolve().parent


@app.get("/")
async def index():
    return FileResponse(
        BASE_DIR / "templates" / "index.html"
    )


@app.get("/health")
async def health_check():
    return {"status": "ok"}


app.include_router(leads_router)


@app.get("/admin")
async def admin(authorized: bool = Depends(verify_admin)):
    return FileResponse(
        BASE_DIR / "templates" / "admin.html"
    )
