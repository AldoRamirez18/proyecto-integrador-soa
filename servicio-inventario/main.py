import os
from typing import List
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy.orm import Session

from database import ProductoDB, ReservaStockDB, crear_tablas, get_db
from schemas import (
    ProductoCreate,
    ProductoUpdate,
    DescuentoStockRequest,
    ReservaStockRequest,
    ProductoResponse,
)

load_dotenv(dotenv_path="../.env")
API_KEY_ESPERADA = os.getenv("API_KEY", "novastore-soa-2026")

crear_tablas()

app = FastAPI(
    title="Servicio de Inventario - NovaStore SOA",
    description="Microservicio encargado de la gestión del catálogo de productos y control de existencias.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)


def verificar_api_key(api_key: str = Security(api_key_header)):
    if not api_key or api_key != API_KEY_ESPERADA:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Acceso denegado: cabecera x-api-key inválida o ausente."
        )
    return api_key


# 1. CREATE - Registrar un nuevo producto
@app.post("/productos", response_model=ProductoResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(verificar_api_key)])
def crear_producto(producto: ProductoCreate, db: Session = Depends(get_db)):
    existente = db.query(ProductoDB).filter(ProductoDB.nombre == producto.nombre).first()
    if existente:
        raise HTTPException(status_code=400, detail="Ya existe un producto registrado con ese nombre.")

    nuevo_producto = ProductoDB(**producto.model_dump())
    db.add(nuevo_producto)
    db.commit()
    db.refresh(nuevo_producto)
    return nuevo_producto


# 2. READ ALL - Consultar todos los productos
@app.get("/productos", response_model=List[ProductoResponse], dependencies=[Depends(verificar_api_key)])
def listar_productos(db: Session = Depends(get_db)):
    return db.query(ProductoDB).all()


# 3. READ ONE - Consultar un producto por ID
@app.get("/productos/{producto_id}", response_model=ProductoResponse, dependencies=[Depends(verificar_api_key)])
def obtener_producto(producto_id: int, db: Session = Depends(get_db)):
    producto = db.query(ProductoDB).filter(ProductoDB.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail=f"Producto con ID {producto_id} no encontrado.")
    return producto


# 4. UPDATE - Actualizar datos de un producto
@app.put("/productos/{producto_id}", response_model=ProductoResponse, dependencies=[Depends(verificar_api_key)])
def actualizar_producto(producto_id: int, datos: ProductoUpdate, db: Session = Depends(get_db)):
    producto = db.query(ProductoDB).filter(ProductoDB.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail=f"Producto con ID {producto_id} no encontrado.")

    cambios = datos.model_dump(exclude_unset=True)
    if "nombre" in cambios and cambios["nombre"] != producto.nombre:
        duplicado = db.query(ProductoDB).filter(ProductoDB.nombre == cambios["nombre"]).first()
        if duplicado:
            raise HTTPException(status_code=400, detail="Ya existe otro producto con ese nombre.")

    for campo, valor in cambios.items():
        setattr(producto, campo, valor)

    db.commit()
    db.refresh(producto)
    return producto


# 5. OPERACIONES DE STOCK - Descontar o reservar existencias
@app.patch("/productos/{producto_id}/descontar", response_model=ProductoResponse, dependencies=[Depends(verificar_api_key)])
def descontar_stock(producto_id: int, solicitud: DescuentoStockRequest, db: Session = Depends(get_db)):
    producto = db.query(ProductoDB).filter(ProductoDB.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail=f"Producto con ID {producto_id} no encontrado.")
    if producto.stock < solicitud.cantidad:
        raise HTTPException(
            status_code=400,
            detail=f"Stock insuficiente. Disponible: {producto.stock}, solicitado: {solicitud.cantidad}."
        )

    producto.stock -= solicitud.cantidad
    db.commit()
    db.refresh(producto)
    return producto


@app.post("/productos/{producto_id}/reservas", response_model=ProductoResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(verificar_api_key)])
def reservar_stock(producto_id: int, solicitud: ReservaStockRequest, db: Session = Depends(get_db)):
    reserva = db.query(ReservaStockDB).filter(
        ReservaStockDB.pedido_id == solicitud.pedido_id
    ).first()
    if reserva:
        if reserva.producto_id != producto_id or reserva.cantidad != solicitud.cantidad:
            raise HTTPException(status_code=409, detail="El pedido ya tiene una reserva diferente.")
        if reserva.estado != "RESERVADA":
            raise HTTPException(status_code=409, detail="La reserva del pedido ya fue liberada.")
        producto = db.query(ProductoDB).filter(ProductoDB.id == producto_id).first()
        if not producto:
            raise HTTPException(status_code=404, detail=f"Producto con ID {producto_id} no encontrado.")
        return producto

    producto = db.query(ProductoDB).filter(ProductoDB.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail=f"Producto con ID {producto_id} no encontrado.")
    stock_actualizado = db.query(ProductoDB).filter(
        ProductoDB.id == producto_id,
        ProductoDB.stock >= solicitud.cantidad,
    ).update(
        {ProductoDB.stock: ProductoDB.stock - solicitud.cantidad},
        synchronize_session=False,
    )
    if not stock_actualizado:
        db.rollback()
        producto = db.query(ProductoDB).filter(ProductoDB.id == producto_id).first()
        raise HTTPException(
            status_code=400,
            detail=f"Stock insuficiente. Disponible: {producto.stock}, solicitado: {solicitud.cantidad}."
        )

    db.add(ReservaStockDB(
        pedido_id=solicitud.pedido_id,
        producto_id=producto_id,
        cantidad=solicitud.cantidad,
        estado="RESERVADA",
    ))
    db.commit()
    db.refresh(producto)
    return producto


@app.post("/productos/{producto_id}/reservas/{pedido_id}/liberar", response_model=ProductoResponse, dependencies=[Depends(verificar_api_key)])
def liberar_reserva(producto_id: int, pedido_id: int, db: Session = Depends(get_db)):
    reserva = db.query(ReservaStockDB).filter(
        ReservaStockDB.pedido_id == pedido_id
    ).first()
    if not reserva or reserva.producto_id != producto_id:
        raise HTTPException(status_code=404, detail="No existe una reserva para ese pedido y producto.")

    producto = db.query(ProductoDB).filter(ProductoDB.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail=f"Producto con ID {producto_id} no encontrado.")
    if reserva.estado == "RESERVADA":
        producto.stock += reserva.cantidad
        reserva.estado = "LIBERADA"
        db.commit()
        db.refresh(producto)
    return producto


# 6. DELETE - Eliminar un producto
@app.delete("/productos/{producto_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(verificar_api_key)])
def eliminar_producto(producto_id: int, db: Session = Depends(get_db)):
    producto = db.query(ProductoDB).filter(ProductoDB.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail=f"Producto con ID {producto_id} no encontrado.")

    db.delete(producto)
    db.commit()
    return None