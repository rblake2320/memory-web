"""Main application entry point."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routers import status, retention, ingest, search, settings_router, chat, memory, health

app = FastAPI()

origins = [
    "http://localhost",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(status.router)
app.include_router(retention.router)
app.include_router(ingest.router)
app.include_router(search.router)
app.include_router(settings_router.router)
app.include_router(chat.router)
app.include_router(memory.router)
app.include_router(health.router)
