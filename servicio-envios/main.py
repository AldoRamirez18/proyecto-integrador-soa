import os
import uuid
from typing import List
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy.orm import Session

from database import EnvioDB, crear_tablas, get_db
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
    db.commit()
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


# 4. UPDATE - Actualizar estado de entrega o dirección
@app.put("/envios/{envio_id}", response_model=EnvioResponse, dependencies=[Depends(verificar_api_key)])
def actualizar_envio(envio_id: int, datos: EnvioUpdate, db: Session = Depends(get_db)):
    envio = db.query(EnvioDB).filter(EnvioDB.id == envio_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail=f"Envío con ID {envio_id} no encontrado.")

    for campo, valor in datos.model_dump(exclude_unset=True).items():
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

    db.delete(envio)
    db.commit()
    return None