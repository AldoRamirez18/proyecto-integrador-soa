import os
from datetime import timedelta
from typing import List
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import PedidoDB, crear_tablas, get_db, hora_actual_utc
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


def marcar_pedido(db: Session, pedido: PedidoDB, estado: str) -> None:
    pedido.estado = estado
    db.commit()


def liberar_reserva(producto_id: int, pedido_id: int, headers: dict) -> None:
    try:
        respuesta = httpx.post(
            f"{INVENTARIO_URL}/productos/{producto_id}/reservas/{pedido_id}/liberar",
            headers=headers,
            timeout=5.0,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No fue posible confirmar la liberación de inventario.",
        ) from exc

    if respuesta.status_code not in (200, 404):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=extraer_detalle_error(respuesta, "No fue posible liberar el inventario."),
        )


def cancelar_pedido_sin_envio(
    db: Session,
    pedido: PedidoDB,
    producto_id: int,
    headers: dict,
) -> None:
    try:
        liberar_reserva(producto_id, pedido.id, headers)
    except HTTPException:
        marcar_pedido(db, pedido, "REQUIERE_REVISION")
        raise
    marcar_pedido(db, pedido, "CANCELADO")


def obtener_envio_existente(pedido_id: int, headers: dict) -> httpx.Response:
    return httpx.get(
        f"{ENVIOS_URL}/envios/pedido/{pedido_id}",
        headers=headers,
        timeout=5.0,
    )


# 1. CREATE (ORQUESTACIÓN SOA CON PREVENCIÓN DE DUPLICADOS)
@app.post("/pedidos", response_model=PedidoResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(verificar_api_key)])
def crear_pedido(solicitud: PedidoCreate, db: Session = Depends(get_db)):
    cliente_limpio = solicitud.cliente.strip()
    direccion_limpia = solicitud.direccion.strip()

    # PASO 0: Verificar si recién se creó un pedido idéntico en los últimos 60 segundos
    ventana_tiempo = hora_actual_utc() - timedelta(seconds=60)
    pedido_duplicado = (
        db.query(PedidoDB)
        .filter(
            func.lower(PedidoDB.cliente) == cliente_limpio.lower(),
            func.lower(PedidoDB.direccion) == direccion_limpia.lower(),
            PedidoDB.zona == solicitud.zona,
            PedidoDB.producto_id == solicitud.producto_id,
            PedidoDB.cantidad == solicitud.cantidad,
            or_(
                and_(
                    PedidoDB.estado.in_(["PROCESANDO", "CONFIRMADO", "EN_CAMINO"]),
                    PedidoDB.creado_en >= ventana_tiempo,
                ),
                PedidoDB.estado == "REQUIERE_REVISION",
            ),
        )
        .order_by(PedidoDB.id.desc())
        .first()
    )

    if pedido_duplicado:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Alerta: Recién se había hecho un pedido idéntico "
                f"(Pedido #{pedido_duplicado.id}, Guía: {pedido_duplicado.numero_guia or 'en proceso'}). "
                f"Espera 1 minuto antes de repetir exactamente la misma orden."
            ),
        )

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
    subtotal = round(producto["precio"] * solicitud.cantidad, 2)

    # PASO 2: Registrar orden preliminar en pedidos.db para obtener el ID de pedido
    nuevo_pedido = PedidoDB(
        cliente=cliente_limpio,
        direccion=direccion_limpia,
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

    # PASO 3: Reservar stock de forma idempotente antes de crear el envío
    try:
        resp_reserva = httpx.post(
            f"{INVENTARIO_URL}/productos/{solicitud.producto_id}/reservas",
            json={
                "pedido_id": nuevo_pedido.id,
                "cantidad": solicitud.cantidad,
            },
            headers=headers_internos,
            timeout=5.0
        )
    except httpx.RequestError:
        try:
            resp_reserva = httpx.post(
                f"{INVENTARIO_URL}/productos/{solicitud.producto_id}/reservas",
                json={"pedido_id": nuevo_pedido.id, "cantidad": solicitud.cantidad},
                headers=headers_internos,
                timeout=5.0,
            )
        except httpx.RequestError:
            try:
                cancelar_pedido_sin_envio(db, nuevo_pedido, solicitud.producto_id, headers_internos)
            except HTTPException:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="No se pudo confirmar la reserva ni liberar el inventario. Pedido marcado para revisión.",
                )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="El Servicio de Inventario no respondió. Se verificó la liberación y se canceló el pedido.",
            )

    if resp_reserva.status_code != status.HTTP_201_CREATED:
        try:
            cancelar_pedido_sin_envio(db, nuevo_pedido, solicitud.producto_id, headers_internos)
        except HTTPException:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="La reserva falló y no se pudo confirmar la liberación. Pedido marcado para revisión.",
            )
        raise HTTPException(
            status_code=resp_reserva.status_code,
            detail=extraer_detalle_error(resp_reserva, "No se pudo reservar el inventario."),
        )

    # PASO 4: Crear el envío; el pedido_id hace segura la repetición si se pierde la respuesta
    try:
        resp_env = httpx.post(
            f"{ENVIOS_URL}/envios",
            json={
                "pedido_id": nuevo_pedido.id,
                "destinatario": cliente_limpio,
                "direccion": direccion_limpia,
                "zona": solicitud.zona
            },
            headers=headers_internos,
            timeout=5.0
        )
    except httpx.RequestError:
        try:
            resp_env = obtener_envio_existente(nuevo_pedido.id, headers_internos)
        except httpx.RequestError:
            marcar_pedido(db, nuevo_pedido, "REQUIERE_REVISION")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No se pudo confirmar si Envíos creó la guía. Se conserva la reserva y el pedido requiere revisión.",
            )

        if resp_env.status_code == status.HTTP_404_NOT_FOUND:
            try:
                cancelar_pedido_sin_envio(db, nuevo_pedido, solicitud.producto_id, headers_internos)
            except HTTPException:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="No se creó el envío y no se pudo liberar el inventario. Pedido marcado para revisión.",
                )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="El Servicio de Envíos no respondió. Se liberó la reserva y se canceló el pedido.",
            )

    if resp_env.status_code not in (status.HTTP_200_OK, status.HTTP_201_CREATED):
        try:
            comprobacion_envio = obtener_envio_existente(nuevo_pedido.id, headers_internos)
        except httpx.RequestError:
            marcar_pedido(db, nuevo_pedido, "REQUIERE_REVISION")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No se pudo verificar el envío. Se conserva la reserva y el pedido requiere revisión.",
            )

        if comprobacion_envio.status_code == status.HTTP_200_OK:
            resp_env = comprobacion_envio
        elif comprobacion_envio.status_code != status.HTTP_404_NOT_FOUND:
            marcar_pedido(db, nuevo_pedido, "REQUIERE_REVISION")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Envíos devolvió un error y no se pudo confirmar el resultado. Pedido marcado para revisión.",
            )
        else:
            try:
                cancelar_pedido_sin_envio(db, nuevo_pedido, solicitud.producto_id, headers_internos)
            except HTTPException:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="No se creó el envío y no se pudo liberar el inventario. Pedido marcado para revisión.",
                )
            raise HTTPException(
                status_code=resp_env.status_code,
                detail=extraer_detalle_error(resp_env, "Error al generar el envío. Se liberó la reserva y se canceló el pedido."),
            )

    try:
        datos_envio = resp_env.json()
        envio_id = int(datos_envio["id"])
        costo_envio = float(datos_envio["costo_envio"])
        numero_guia = str(datos_envio["numero_guia"])
    except (KeyError, TypeError, ValueError) as exc:
        marcar_pedido(db, nuevo_pedido, "REQUIERE_REVISION")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Envíos devolvió una respuesta inválida. Se conserva la reserva y el pedido requiere revisión.",
        ) from exc

    # PASO 5: Confirmar pedido con totales reales y número de guía
    nuevo_pedido.costo_envio = costo_envio
    nuevo_pedido.total = round(subtotal + costo_envio, 2)
    nuevo_pedido.numero_guia = numero_guia
    nuevo_pedido.estado = "CONFIRMADO"

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        try:
            cancelacion = httpx.put(
                f"{ENVIOS_URL}/envios/{envio_id}",
                json={"estado": "CANCELADO"},
                headers=headers_internos,
                timeout=5.0,
            )
            if cancelacion.status_code != status.HTTP_200_OK:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No se pudo cancelar el envío.")
            liberar_reserva(solicitud.producto_id, nuevo_pedido.id, headers_internos)
        except (httpx.RequestError, HTTPException):
            marcar_pedido(db, nuevo_pedido, "REQUIERE_REVISION")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Falló el registro del pedido y no se pudo completar la compensación. Pedido marcado para revisión.",
            ) from exc
        marcar_pedido(db, nuevo_pedido, "CANCELADO")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Falló el registro del pedido; se canceló el envío y se liberó el inventario.",
        ) from exc

    db.refresh(nuevo_pedido)
    return nuevo_pedido


@app.get("/pedidos", response_model=List[PedidoResponse], dependencies=[Depends(verificar_api_key)])
def listar_pedidos(db: Session = Depends(get_db)):
    return db.query(PedidoDB).all()


@app.get("/pedidos/{pedido_id}", response_model=PedidoResponse, dependencies=[Depends(verificar_api_key)])
def obtener_pedido(pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(PedidoDB).filter(PedidoDB.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail=f"Pedido con ID {pedido_id} no encontrado.")
    return pedido


@app.put("/pedidos/{pedido_id}", response_model=PedidoResponse, dependencies=[Depends(verificar_api_key)])
def actualizar_pedido(pedido_id: int, datos: PedidoUpdate, db: Session = Depends(get_db)):
    pedido = db.query(PedidoDB).filter(PedidoDB.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail=f"Pedido con ID {pedido_id} no encontrado.")

    transiciones = {
        "CONFIRMADO": {"EN_CAMINO"},
        "EN_CAMINO": {"ENTREGADO"},
        "ENTREGADO": set(),
    }
    if datos.estado == pedido.estado:
        return pedido
    if datos.estado not in transiciones.get(pedido.estado, set()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No se puede cambiar el pedido de {pedido.estado} a {datos.estado}.",
        )

    estado_envio = {"EN_CAMINO": "EN_RUTA", "ENTREGADO": "ENTREGADO"}[datos.estado]
    headers_internos = {"x-api-key": API_KEY_ESPERADA}
    try:
        resp_envio = obtener_envio_existente(pedido.id, headers_internos)
        if resp_envio.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status_code=409, detail="No existe un envío asociado a este pedido.")
        if resp_envio.status_code != status.HTTP_200_OK:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=extraer_detalle_error(resp_envio, "No se pudo consultar el envío asociado."),
            )

        envio = resp_envio.json()
        if envio["estado"] != estado_envio:
            cambio = httpx.put(
                f"{ENVIOS_URL}/envios/{envio['id']}",
                json={"estado": estado_envio},
                headers=headers_internos,
                timeout=5.0,
            )
            if cambio.status_code != status.HTTP_200_OK:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=extraer_detalle_error(cambio, "No se pudo actualizar el estado del envío."),
                )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El Servicio de Envíos no está disponible; el estado del pedido no fue modificado.",
        ) from exc

    pedido.estado = datos.estado
    db.commit()
    db.refresh(pedido)
    return pedido


@app.delete("/pedidos/{pedido_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(verificar_api_key)])
def eliminar_pedido(pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(PedidoDB).filter(PedidoDB.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail=f"Pedido con ID {pedido_id} no encontrado.")

    db.delete(pedido)
    db.commit()
    return None