from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import declarative_base, sessionmaker

SQLALCHEMY_DATABASE_URL = "sqlite:///./inventario.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class ProductoDB(Base):
    __tablename__ = "productos"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, unique=True, index=True, nullable=False)
    descripcion = Column(String, nullable=True)
    precio = Column(Float, nullable=False)
    stock = Column(Integer, nullable=False)


class ReservaStockDB(Base):
    __tablename__ = "reservas_stock"

    pedido_id = Column(Integer, primary_key=True)
    producto_id = Column(Integer, nullable=False)
    cantidad = Column(Integer, nullable=False)
    estado = Column(String, default="RESERVADA", nullable=False)


def crear_tablas():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()