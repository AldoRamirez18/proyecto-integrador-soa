from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

SQLALCHEMY_DATABASE_URL = "sqlite:///./pedidos.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def hora_actual_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PedidoDB(Base):
    __tablename__ = "pedidos"

    id = Column(Integer, primary_key=True, index=True)
    cliente = Column(String, nullable=False)
    direccion = Column(String, nullable=False)
    zona = Column(String, nullable=False)
    producto_id = Column(Integer, nullable=False)
    producto_nombre = Column(String, nullable=False)
    cantidad = Column(Integer, nullable=False)
    subtotal = Column(Float, nullable=False)
    costo_envio = Column(Float, nullable=False)
    total = Column(Float, nullable=False)
    numero_guia = Column(String, nullable=True)
    estado = Column(String, default="PROCESANDO", nullable=False)
    creado_en = Column(DateTime, default=hora_actual_utc, nullable=False)


def crear_tablas():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()