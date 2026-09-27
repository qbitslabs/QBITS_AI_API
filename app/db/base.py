# AI Postgres access: base.
# SQLAlchemy models/session for the dedicated AI database only.
from sqlalchemy.orm import declarative_base

Base = declarative_base()
