import os
import uuid
from typing import List
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import EnvioDB, EnvioPedidoDB, crear_tablas, get_db
from schemas import EnvioCreate, EnvioUpdate, EnvioResponse

load_dotenv(dotenv_path="../.env")
API_KEY_ESPERADA = os.getenv("API_KEY", "novastore-soa-2026")

TARIFAS_ZONA = {
    "LOCAL": 50.00,
    "NACIONAL": 120.00,
    "EXPRESS": 200.00
}

crear_tablas()

app = FastAPI(
    title="Servicio de Envíos y Logística - NovaStore SOA",
    description="Microservicio encargado de calcular costos de envío, generar guías y actualizar estados de entrega.",
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


# 1. CREATE - Generar solicitud de envío, calcular costo y asignar guía
@app.post("/envios", response_model=EnvioResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(verificar_api_key)])
def crear_envio(envio: EnvioCreate, db: Session = Depends(get_db)):
    relacion = db.query(EnvioPedidoDB).filter(
        EnvioPedidoDB.pedido_id == envio.pedido_id
    ).first()
    envios_existentes = [] if relacion else db.query(EnvioDB).filter(
        EnvioDB.pedido_id == envio.pedido_id
    ).order_by(EnvioDB.id.asc()).all()
    if not relacion and len(envios_existentes) > 1:
        raise HTTPException(
            status_code=409,
            detail=f"El pedido {envio.pedido_id} tiene envíos históricos duplicados y requiere revisión.",
        )
    existente = (
        db.query(EnvioDB).filter(EnvioDB.id == relacion.envio_id).first()
        if relacion
        else (envios_existentes[0] if envios_existentes else None)
    )
    if existente:
        if (
            existente.destinatario != envio.destinatario
            or existente.direccion != envio.direccion
            or existente.zona != envio.zona
        ):
            raise HTTPException(status_code=409, detail="El pedido ya tiene un envío con datos diferentes.")
        if existente.estado == "CANCELADO":
            raise HTTPException(status_code=409, detail="El envío de este pedido ya fue cancelado.")
        return existente

    costo = TARIFAS_ZONA[envio.zona]
    guia = f"NV-{uuid.uuid4().hex[:8].upper()}"

    nuevo_envio = EnvioDB(
        pedido_id=envio.pedido_id,
        destinatario=envio.destinatario,
        direccion=envio.direccion,
        zona=envio.zona,
        costo_envio=costo,
        numero_guia=guia,
        estado="PREPARANDO"
    )
    db.add(nuevo_envio)
    db.flush()
    db.add(EnvioPedidoDB(pedido_id=envio.pedido_id, envio_id=nuevo_envio.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        relacion = db.query(EnvioPedidoDB).filter(
            EnvioPedidoDB.pedido_id == envio.pedido_id
        ).first()
        if not relacion:
            raise
        existente = db.query(EnvioDB).filter(EnvioDB.id == relacion.envio_id).first()
        if not existente:
            raise
        if (
            existente.destinatario != envio.destinatario
            or existente.direccion != envio.direccion
            or existente.zona != envio.zona
        ):
            raise HTTPException(status_code=409, detail="El pedido ya tiene un envío con datos diferentes.")
        if existente.estado == "CANCELADO":
            raise HTTPException(status_code=409, detail="El envío de este pedido ya fue cancelado.")
        return existente
    db.refresh(nuevo_envio)
    return nuevo_envio


# 2. READ ALL - Consultar todos los envíos
@app.get("/envios", response_model=List[EnvioResponse], dependencies=[Depends(verificar_api_key)])
def listar_envios(db: Session = Depends(get_db)):
    return db.query(EnvioDB).all()


# 3. READ ONE - Consultar envío por ID
@app.get("/envios/{envio_id}", response_model=EnvioResponse, dependencies=[Depends(verificar_api_key)])
def obtener_envio(envio_id: int, db: Session = Depends(get_db)):
    envio = db.query(EnvioDB).filter(EnvioDB.id == envio_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail=f"Envío con ID {envio_id} no encontrado.")
    return envio


@app.get("/envios/pedido/{pedido_id}", response_model=EnvioResponse, dependencies=[Depends(verificar_api_key)])
def obtener_envio_por_pedido(pedido_id: int, db: Session = Depends(get_db)):
    relacion = db.query(EnvioPedidoDB).filter(
        EnvioPedidoDB.pedido_id == pedido_id
    ).first()
    if relacion:
        envio = db.query(EnvioDB).filter(EnvioDB.id == relacion.envio_id).first()
    else:
        envios = db.query(EnvioDB).filter(EnvioDB.pedido_id == pedido_id).all()
        if len(envios) > 1:
            raise HTTPException(
                status_code=409,
                detail=f"El pedido {pedido_id} tiene envíos históricos duplicados y requiere revisión.",
            )
        envio = envios[0] if envios else None
    if not envio:
        raise HTTPException(status_code=404, detail=f"No hay envío para el pedido {pedido_id}.")
    return envio


# 4. UPDATE - Actualizar estado de entrega o dirección
@app.put("/envios/{envio_id}", response_model=EnvioResponse, dependencies=[Depends(verificar_api_key)])
def actualizar_envio(envio_id: int, datos: EnvioUpdate, db: Session = Depends(get_db)):
    envio = db.query(EnvioDB).filter(EnvioDB.id == envio_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail=f"Envío con ID {envio_id} no encontrado.")

    cambios = datos.model_dump(exclude_unset=True)
    transiciones = {
        "PREPARANDO": {"EN_RUTA", "CANCELADO"},
        "EN_RUTA": {"ENTREGADO", "CANCELADO"},
        "ENTREGADO": set(),
        "CANCELADO": set(),
    }
    if "estado" in cambios and cambios["estado"] != envio.estado:
        if cambios["estado"] not in transiciones[envio.estado]:
            raise HTTPException(
                status_code=409,
                detail=f"No se puede cambiar el envío de {envio.estado} a {cambios['estado']}."
            )

    for campo, valor in cambios.items():
        setattr(envio, campo, valor)

    db.commit()
    db.refresh(envio)
    return envio


# 5. DELETE - Eliminar registro de envío
@app.delete("/envios/{envio_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(verificar_api_key)])
def eliminar_envio(envio_id: int, db: Session = Depends(get_db)):
    envio = db.query(EnvioDB).filter(EnvioDB.id == envio_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail=f"Envío con ID {envio_id} no encontrado.")

    relacion = db.query(EnvioPedidoDB).filter(
        EnvioPedidoDB.envio_id == envio_id
    ).first()
    if relacion:
        db.delete(relacion)
    db.delete(envio)
    db.commit()
    return None