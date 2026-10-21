from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from backend.app.config import settings

# Configure engine based on dialect (handle SQLite specifics like check_same_thread)
connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    # Import all models to ensure they are registered with Base metadata
    from backend.app.models import company, signal, feature, score, user, review  # noqa
    Base.metadata.create_all(bind=engine)

    # SQLite migration helper for new columns
    if settings.DATABASE_URL.startswith("sqlite"):
        with engine.connect() as conn:
            cursor = conn.exec_driver_sql("PRAGMA table_info(stream_checkpoints)")
            columns = [row[1] for row in cursor.fetchall()]
            if columns:
                if "has_gap" not in columns:
                    conn.exec_driver_sql("ALTER TABLE stream_checkpoints ADD COLUMN has_gap BOOLEAN DEFAULT 0")
                if "gap_from_timepoint" not in columns:
                    conn.exec_driver_sql("ALTER TABLE stream_checkpoints ADD COLUMN gap_from_timepoint VARCHAR(255)")
                if "gap_to_timepoint" not in columns:
                    conn.exec_driver_sql("ALTER TABLE stream_checkpoints ADD COLUMN gap_to_timepoint VARCHAR(255)")
                if "gap_detected_at" not in columns:
                    conn.exec_driver_sql("ALTER TABLE stream_checkpoints ADD COLUMN gap_detected_at DATETIME")
            conn.commit()
