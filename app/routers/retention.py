from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, CSVResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from typing import List
import csv
import io
from app.database import get_db
from app.models import Memory
from sqlalchemy.orm import Session

router = APIRouter()

class MemoryExport(BaseModel):
    id: int
    content: str
    created_at: str
    helpful_count: int
    retrieval_count: int

@router.get("/api/memories/export", response_class=JSONResponse)
async def export_memories(format: str = Query(...), db: Session = Depends(get_db)):
    if format not in ["json", "csv"]:
        raise HTTPException(status_code=400, detail="Invalid format. Only json and csv are supported.")

    memories = db.query(Memory).all()

    if format == "json":
        return jsonable_encoder([{"id": memory.id, "content": memory.content, "created_at": str(memory.created_at), "helpful_count": memory.helpful_count, "retrieval_count": memory.retrieval_count} for memory in memories])

    # Generate CSV
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["id", "content", "created_at", "helpful_count", "retrieval_count"])
    writer.writeheader()
    for memory in memories:
        writer.writerow({"id": memory.id, "content": memory.content, "created_at": str(memory.created_at), "helpful_count": memory.helpful_count, "retrieval_count": memory.retrieval_count})

    return CSVResponse(content=output.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": "attachment;filename=memories.csv"
    })

