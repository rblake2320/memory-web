from sqlalchemy import Column, Integer, String, DateTime, Float
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class Memory(Base):
    __tablename__ = "memories"

    id = Column(Integer, primary_key=True, index=True)
    content = Column(String, index=True)
    created_at = Column(DateTime, index=True)
    helpful_count = Column(Integer, index=True)
    retrieval_count = Column(Integer, index=True)

