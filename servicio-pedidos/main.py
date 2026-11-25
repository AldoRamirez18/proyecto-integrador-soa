import os
from typing import List
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy.orm import Session

from database import PedidoDB, crear_tablas, get_db
from schemas import PedidoCreate, PedidoUpdate, PedidoResponse

load_dotenv(dotenv_path="../.env")
API_KEY_ESPERADA = os.getenv("API_KEY", "novastore-soa-2026")
INVENTARIO_URL = os.getenv("INVENTARIO_URL", "http://127.0.0.1:8001")
ENVIOS_URL = os.getenv("ENVIOS_URL", "http://127.0.0.1:8002")

crear_tablas()

app = FastAPI(
    title="Servicio Orquestador de Pedidos - NovaStore SOA",
    description="Microservicio orquestador que integra Inventario y Envíos para procesar órdenes de compra.",
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


def extraer_detalle_error(respuesta: httpx.Response, mensaje_defecto: str) -> str:
    try:
        return respuesta.json().get("detail", mensaje_defecto)
    except Exception:
        return mensaje_defecto


# 1. CREATE (ORQUESTACIÓN SOA COMPLETA)
@app.post("/pedidos", response_model=PedidoResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(verificar_api_key)])
def crear_pedido(solicitud: PedidoCreate, db: Session = Depends(get_db)):
    headers_internos = {"x-api-key": API_KEY_ESPERADA}

    # PASO 1: Consultar producto y validar existencias en Servicio de Inventario (8001)
    try:
        resp_inv = httpx.get(
            f"{INVENTARIO_URL}/productos/{solicitud.producto_id}",
            headers=headers_internos,
            timeout=5.0
        )
    except httpx.RequestError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Error de integración: el Servicio de Inventario (8001) no está disponible."
        )

    if resp_inv.status_code != 200:
        raise HTTPException(
            status_code=resp_inv.status_code,
            detail=extraer_detalle_error(resp_inv, "Error al consultar el producto en Inventario.")
        )

    producto = resp_inv.json()
    if producto["stock"] < solicitud.cantidad:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stock insuficiente para '{producto['nombre']}'. Disponible: {producto['stock']}, solicitado: {solicitud.cantidad}."
        )

    subtotal = round(producto["precio"] * solicitud.cantidad, 2)

    # PASO 2: Registrar orden preliminar en pedidos.db para obtener el ID de pedido
    nuevo_pedido = PedidoDB(
        cliente=solicitud.cliente,
        direccion=solicitud.direccion,
        zona=solicitud.zona,
        producto_id=solicitud.producto_id,
        producto_nombre=producto["nombre"],
        cantidad=solicitud.cantidad,
        subtotal=subtotal,
        costo_envio=0.0,
        total=subtotal,
        estado="PROCESANDO"
    )
    db.add(nuevo_pedido)
    db.commit()
    db.refresh(nuevo_pedido)

    # PASO 3: Solicitar cálculo de tarifa y guía al Servicio de Envíos (8002)
    try:
        resp_env = httpx.post(
            f"{ENVIOS_URL}/envios",
            json={
                "pedido_id": nuevo_pedido.id,
                "destinatario": solicitud.cliente,
                "direccion": solicitud.direccion,
                "zona": solicitud.zona
            },
            headers=headers_internos,
            timeout=5.0
        )
    except httpx.RequestError:
        nuevo_pedido.estado = "CANCELADO"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Error de integración: el Servicio de Envíos (8002) no está disponible. Pedido cancelado."
        )

    if resp_env.status_code != 201:
        nuevo_pedido.estado = "CANCELADO"
        db.commit()
        raise HTTPException(
            status_code=resp_env.status_code,
            detail=extraer_detalle_error(resp_env, "Error al generar el envío.")
        )

    datos_envio = resp_env.json()

    # PASO 4: Descontar stock en Servicio de Inventario (8001)
    try:
        resp_desc = httpx.patch(
            f"{INVENTARIO_URL}/productos/{solicitud.producto_id}/descontar",
            json={"cantidad": solicitud.cantidad},
            headers=headers_internos,
            timeout=5.0
        )
    except httpx.RequestError:
        # Compensación: cancelar el envío creado si Inventario falló
        httpx.put(
            f"{ENVIOS_URL}/envios/{datos_envio['id']}",
            json={"estado": "CANCELADO"},
            headers=headers_internos,
            timeout=5.0
        )
        nuevo_pedido.estado = "CANCELADO"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Fallo al descontar stock en Inventario. Se revirtió el envío y se canceló el pedido."
        )

    if resp_desc.status_code != 200:
        httpx.put(
            f"{ENVIOS_URL}/envios/{datos_envio['id']}",
            json={"estado": "CANCELADO"},
            headers=headers_internos,
            timeout=5.0
        )
        nuevo_pedido.estado = "CANCELADO"
        db.commit()
        raise HTTPException(
            status_code=resp_desc.status_code,
            detail=extraer_detalle_error(resp_desc, "No se pudo descontar el stock.")
        )

    # PASO 5: Confirmar pedido con totales reales y número de guía
    nuevo_pedido.costo_envio = datos_envio["costo_envio"]
    nuevo_pedido.total = round(subtotal + datos_envio["costo_envio"], 2)
    nuevo_pedido.numero_guia = datos_envio["numero_guia"]
    nuevo_pedido.estado = "CONFIRMADO"

    db.commit()
    db.refresh(nuevo_pedido)
    return nuevo_pedido


# 2. READ ALL - Listar todos los pedidos
@app.get("/pedidos", response_model=List[PedidoResponse], dependencies=[Depends(verificar_api_key)])
def listar_pedidos(db: Session = Depends(get_db)):
    return db.query(PedidoDB).all()


# 3. READ ONE - Consultar pedido por ID
@app.get("/pedidos/{pedido_id}", response_model=PedidoResponse, dependencies=[Depends(verificar_api_key)])
def obtener_pedido(pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(PedidoDB).filter(PedidoDB.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail=f"Pedido con ID {pedido_id} no encontrado.")
    return pedido


# 4. UPDATE - Actualizar estado del pedido
@app.put("/pedidos/{pedido_id}", response_model=PedidoResponse, dependencies=[Depends(verificar_api_key)])
def actualizar_pedido(pedido_id: int, datos: PedidoUpdate, db: Session = Depends(get_db)):
    pedido = db.query(PedidoDB).filter(PedidoDB.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail=f"Pedido con ID {pedido_id} no encontrado.")

    pedido.estado = datos.estado
    db.commit()
    db.refresh(pedido)
    return pedido


# 5. DELETE - Eliminar pedido
@app.delete("/pedidos/{pedido_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(verificar_api_key)])
def eliminar_pedido(pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(PedidoDB).filter(PedidoDB.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail=f"Pedido con ID {pedido_id} no encontrado.")

    db.delete(pedido)
    db.commit()
    return None