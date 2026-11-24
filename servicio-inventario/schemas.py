from pydantic import BaseModel, Field
from typing import Optional


class ProductoCreate(BaseModel):
    nombre: str = Field(..., min_length=2, max_length=100, examples=["Laptop Pro 15"])
    descripcion: Optional[str] = Field(None, max_length=250, examples=["16GB RAM, 512GB SSD"])
    precio: float = Field(..., gt=0, description="El precio debe ser mayor a 0", examples=[15500.00])
    stock: int = Field(..., ge=0, description="El stock no puede ser negativo", examples=[10])


class ProductoUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=2, max_length=100)
    descripcion: Optional[str] = Field(None, max_length=250)
    precio: Optional[float] = Field(None, gt=0)
    stock: Optional[int] = Field(None, ge=0)


class DescuentoStockRequest(BaseModel):
    cantidad: int = Field(..., gt=0, description="Cantidad de unidades a descontar", examples=[2])


class ProductoResponse(BaseModel):
    id: int
    nombre: str
    descripcion: Optional[str]
    precio: float
    stock: int

    model_config = {"from_attributes": True}