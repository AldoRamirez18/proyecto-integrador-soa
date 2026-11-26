from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import declarative_base, sessionmaker

SQLALCHEMY_DATABASE_URL = "sqlite:///./envios.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class EnvioDB(Base):
    __tablename__ = "envios"

    id = Column(Integer, primary_key=True, index=True)
    pedido_id = Column(Integer, index=True, nullable=False)
    destinatario = Column(String, nullable=False)
    direccion = Column(String, nullable=False)
    zona = Column(String, nullable=False)
    costo_envio = Column(Float, nullable=False)
    numero_guia = Column(String, unique=True, index=True, nullable=False)
    estado = Column(String, default="PREPARANDO", nullable=False)


class EnvioPedidoDB(Base):
    __tablename__ = "envios_por_pedido"

    pedido_id = Column(Integer, primary_key=True)
    envio_id = Column(Integer, unique=True, nullable=False)


def crear_tablas():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()