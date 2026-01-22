from fastapi import FastAPI

from app.routes import admin, chat, crawl, health, ingest_jobs
from app.utils.db import init_db
from app.utils.logging import setup_logging
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5000",
        "http://127.0.0.1:5000",

        # If you ever access the frontend using your Windows IP instead of localhost:
        "http://192.168.30.11:5000",

        # If you ever access via WSL IP directly:
        "http://172.31.225.208:5000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    setup_logging()
    init_db()


app.include_router(chat.router)
app.include_router(admin.router)
app.include_router(crawl.router)
app.include_router(health.router)
app.include_router(ingest_jobs.router)
