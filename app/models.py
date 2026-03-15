from typing import Optional
from sqlalchemy import Column, Integer, String, ForeignKey, Table
from sqlalchemy.orm import relationship
from ..database import Base

# Create a new table for memory tags
memory_tags_table = Table(
    "memory_tags",
    Base.metadata,
    Column("memory_id", Integer, ForeignKey("memory.id")),
    Column("tag", String(50), index=True),
)

class MemoryTag(Base):
    __tablename__ = "memory_tags"
    id = Column(Integer, primary_key=True)
    memory_id = Column(Integer, ForeignKey("memory.id"))
    tag = Column(String(50), index=True)

    memory = relationship("Memory", back_populates="tags")

class Memory(Base):
    # existing code...
    tags = relationship("MemoryTag", back_populates="memory")

