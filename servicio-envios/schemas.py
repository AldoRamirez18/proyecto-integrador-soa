from pydantic import BaseModel, Field
from typing import Optional, Literal


class EnvioCreate(BaseModel):
    pedido_id: int = Field(..., gt=0, examples=[1])
    destinatario: str = Field(..., min_length=2, max_length=100, examples=["Aldo Ramírez"])
    direccion: str = Field(..., min_length=5, max_length=250, examples=["Av. Juárez 123, Puebla"])
    zona: Literal["LOCAL", "NACIONAL", "EXPRESS"] = Field(
        default="LOCAL",
        description="Zona de envío: LOCAL ($50), NACIONAL ($120) o EXPRESS ($200)"
    )


class EnvioUpdate(BaseModel):
    direccion: Optional[str] = Field(None, min_length=5, max_length=250)
    estado: Optional[Literal["PREPARANDO", "EN_RUTA", "ENTREGADO", "CANCELADO"]] = None


class EnvioResponse(BaseModel):
    id: int
    pedido_id: int
    destinatario: str
    direccion: str
    zona: str
    costo_envio: float
    numero_guia: str
    estado: str

    model_config = {"from_attributes": True}