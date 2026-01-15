from fastapi import FastAPI

from app.routes import admin, chat
from app.utils.db import init_db
from app.utils.logging import setup_logging

app = FastAPI()


@app.on_event("startup")
async def startup() -> None:
    setup_logging()
    init_db()


app.include_router(chat.router)
app.include_router(admin.router)
