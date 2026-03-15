from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from ..config import settings

# Create a new table for memory tags
def create_tables():
    engine = create_engine(settings.DATABASE_URL)
    Base.metadata.create_all(bind=engine)

# existing code...

