"""Models."""
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean
from app.database import Base

class Memory(Base):
    __tablename__ = "memories"

    id = Column(Integer, primary_key=True)
    fact = Column(String)
    category = Column(String, nullable=True)
    confidence = Column(Float, nullable=True)
    importance = Column(Float, nullable=True)
    tombstoned_at = Column(DateTime, nullable=True)
    access_count = Column(Integer)
    created_at = Column(DateTime, nullable=True)
