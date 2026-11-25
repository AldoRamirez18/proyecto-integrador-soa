from pydantic import BaseModel, Field
from typing import Optional, Literal


class PedidoCreate(BaseModel):
    cliente: str = Field(..., min_length=2, max_length=100, examples=["Aldo Ramírez"])
    direccion: str = Field(..., min_length=5, max_length=250, examples=["Av. Juárez 123, Puebla"])
    zona: Literal["LOCAL", "NACIONAL", "EXPRESS"] = Field(default="LOCAL")
    producto_id: int = Field(..., gt=0, examples=[1])
    cantidad: int = Field(..., gt=0, description="Debe pedir al menos 1 unidad", examples=[2])


class PedidoUpdate(BaseModel):
    estado: Literal["CONFIRMADO", "EN_CAMINO", "ENTREGADO", "CANCELADO"]


class PedidoResponse(BaseModel):
    id: int
    cliente: str
    direccion: str
    zona: str
    producto_id: int
    producto_nombre: str
    cantidad: int
    subtotal: float
    costo_envio: float
    total: float
    numero_guia: Optional[str]
    estado: str

    model_config = {"from_attributes": True}