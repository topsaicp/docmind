from sqlalchemy import create_engine, Column, String, Integer, Float, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "kb.db"
engine  = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
Session = sessionmaker(bind=engine)
Base    = declarative_base()


class Document(Base):
    __tablename__ = "documents"

    id          = Column(String, primary_key=True)   # uuid
    filename    = Column(String, nullable=False)
    original_name = Column(String, nullable=False)
    size_bytes  = Column(Integer, default=0)
    page_count  = Column(Integer, default=0)
    chunk_count = Column(Integer, default=0)
    status      = Column(String, default="pending")  # pending/processing/ready/error
    error_msg   = Column(Text, default="")
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def init_db():
    Base.metadata.create_all(engine)


def get_session():
    session = Session()
    try:
        yield session
    finally:
        session.close()
