from fastapi import FastAPI
from app.routers import status, retention, ingest

app = FastAPI()

app.include_router(status.router)
app.include_router(retention.router)
app.include_router(ingest.router)

