from sqlalchemy import create_engine, Column, String, Integer, DateTime, Text, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from pathlib import Path
import os, uuid

_DATABASE_URL = os.getenv("DATABASE_URL", "")

if _DATABASE_URL:
    # 生产环境：PostgreSQL (Supabase / Railway Postgres)
    engine = create_engine(_DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
else:
    # 本地开发：SQLite
    DB_PATH = Path(__file__).parent.parent / "kb.db"
    engine  = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})

Session = sessionmaker(bind=engine)
Base    = declarative_base()


class User(Base):
    __tablename__ = "users"

    id                = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email             = Column(String, unique=True, nullable=False, index=True)
    password_hash     = Column(String, nullable=False)
    plan              = Column(String, default="free")      # free / pro
    plan_expires_at   = Column(DateTime, nullable=True)
    pdf_count         = Column(Integer, default=0)
    query_count_today = Column(Integer, default=0)
    query_date        = Column(String, nullable=True)       # "2026-06-23"
    is_admin          = Column(Boolean, default=False)
    email_verified    = Column(Boolean, default=False)
    email_verify_token      = Column(String, nullable=True)
    email_verify_expires_at = Column(DateTime, nullable=True)
    created_at        = Column(DateTime, default=datetime.utcnow)


class Document(Base):
    __tablename__ = "documents"

    id            = Column(String, primary_key=True)
    user_id       = Column(String, nullable=True, index=True)
    filename      = Column(String, nullable=False)
    original_name = Column(String, nullable=False)
    size_bytes    = Column(Integer, default=0)
    page_count    = Column(Integer, default=0)
    chunk_count   = Column(Integer, default=0)
    status        = Column(String, default="pending")
    error_msg     = Column(Text, default="")
    created_at    = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def init_db():
    Base.metadata.create_all(engine)


def get_session():
    session = Session()
    try:
        yield session
    finally:
        session.close()
