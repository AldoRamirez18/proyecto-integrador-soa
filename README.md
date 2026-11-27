# NovaStore SOA

Prototipo educativo de una tienda que integra tres servicios REST independientes para administrar productos, pedidos y envíos. El Servicio de Pedidos coordina la compra de extremo a extremo; no es un CRUD monolítico.

## Arquitectura

| Componente | Responsabilidad | Puerto | Persistencia |
| --- | --- | ---: | --- |
| Cliente web | Consultar el catálogo, registrar productos, crear pedidos y avanzar entregas | 8000 | Ninguna |
| Servicio de Inventario | Catálogo, existencias y reservas de stock | 8001 | `servicio-inventario/inventario.db` |
| Servicio de Envíos | Tarifas, guías y estado de entrega | 8002 | `servicio-envios/envios.db` |
| Servicio de Pedidos | Orquestar la compra, registrar pedidos y sincronizar estados | 8003 | `servicio-pedidos/pedidos.db` |

Los servicios exponen APIs REST con JSON y generan su documentación interactiva OpenAPI/Swagger en `/docs`. Las llamadas protegidas requieren la cabecera `x-api-key`. Cada servicio administra su propia base SQLite; no se comparten tablas ni se usa un broker de mensajes.

La arquitectura SOA, las operaciones principales, el flujo de compra y los diagramas se describen en [docs/arquitectura-soa.md](docs/arquitectura-soa.md).

## Requisitos

- Windows 10/11 y PowerShell.
- Python 3.10 o posterior.
- Git para clonar el repositorio.
- Navegador web moderno.

## Instalación

Desde la carpeta raíz del proyecto:

```powershell
py -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

La clave de ejemplo debe coincidir con la que se introduce en el campo `x-api-key` del cliente web. Para esta demostración local, ambas usan `novastore-soa-2026`. Si la cambias en `.env`, introduce el mismo valor en el cliente.

> **Seguridad:** esta clave es únicamente para la demostración local. El cliente web la envía desde el navegador, por lo que no es un secreto seguro para producción. No publiques credenciales reales; `.env` está excluido de Git. La API y el cliente usan HTTP y están configurados para ejecución local.

## Ejecución

Abre cuatro terminales PowerShell en la carpeta raíz del proyecto. Activa el entorno virtual en cada terminal, inicia un componente por terminal y deja los procesos ejecutándose.

**Terminal 1 — Inventario:**

```powershell
.\venv\Scripts\Activate.ps1
Set-Location servicio-inventario
uvicorn main:app --host 127.0.0.1 --port 8001 --reload
```

**Terminal 2 — Envíos:**

```powershell
.\venv\Scripts\Activate.ps1
Set-Location servicio-envios
uvicorn main:app --host 127.0.0.1 --port 8002 --reload
```

**Terminal 3 — Pedidos:**

```powershell
.\venv\Scripts\Activate.ps1
Set-Location servicio-pedidos
uvicorn main:app --host 127.0.0.1 --port 8003 --reload
```

**Terminal 4 — Cliente web:**

```powershell
.\venv\Scripts\Activate.ps1
Set-Location cliente
python -m http.server 8000 --bind 127.0.0.1
```

Abre <http://127.0.0.1:8000>. Las bases de datos y tablas se crean al iniciar cada servicio si aún no existen. Conserva los archivos `.db` entre ejecuciones si quieres mantener los datos.

## URLs útiles

| Componente | Interfaz o documentación |
| --- | --- |
| Cliente NovaStore | <http://127.0.0.1:8000> |
| Inventario Swagger | <http://127.0.0.1:8001/docs> |
| Envíos Swagger | <http://127.0.0.1:8002/docs> |
| Pedidos Swagger | <http://127.0.0.1:8003/docs> |
| OpenAPI JSON de Inventario | <http://127.0.0.1:8001/openapi.json> |
| OpenAPI JSON de Envíos | <http://127.0.0.1:8002/openapi.json> |
| OpenAPI JSON de Pedidos | <http://127.0.0.1:8003/openapi.json> |

Para probar operaciones protegidas desde Swagger, usa **Authorize** y proporciona la clave configurada como `x-api-key`.

## Demostración recomendada

1. Confirma que los tres servicios muestran su documentación en `/docs` y abre el cliente.
2. Registra un producto con existencias suficientes; verifica el catálogo en Inventario.
3. Envía un pedido desde NovaStore. Comprueba en Pedidos el estado `CONFIRMADO` y su guía; verifica que el stock disminuyó y que Envíos creó una guía.
4. Marca el envío en ruta desde la tabla de logística. El pedido debe pasar a `EN_CAMINO` y el envío a `EN_RUTA`.
5. Marca el envío como entregado. Ambos registros deben terminar en `ENTREGADO`.
6. Para demostrar validaciones, intenta pedir una cantidad superior al stock o usa un identificador de producto inexistente.

## Contratos y operaciones

Las rutas y modelos exactos están publicados por cada servicio en su OpenAPI (`/docs` y `/openapi.json`). Operaciones principales:

- **Inventario:** `GET/POST /productos`, `GET/PUT/DELETE /productos/{producto_id}`, `POST /productos/{producto_id}/reservas`, `POST /productos/{producto_id}/reservas/{pedido_id}/liberar`.
- **Envíos:** `POST/GET /envios`, `GET/PUT/DELETE /envios/{envio_id}`, `GET /envios/pedido/{pedido_id}`.
- **Pedidos:** `POST/GET /pedidos`, `GET/PUT/DELETE /pedidos/{pedido_id}`.

El endpoint `PATCH /productos/{producto_id}/descontar` también está disponible para descontar existencias de forma directa; el flujo orquestado de compra utiliza reservas.

## Configuración

La plantilla [.env.example](.env.example) contiene:

```dotenv
API_KEY=novastore-soa-2026
INVENTARIO_URL=http://127.0.0.1:8001
ENVIOS_URL=http://127.0.0.1:8002
PEDIDOS_URL=http://127.0.0.1:8003
```

Inventario y Envíos leen `API_KEY`; Pedidos también lee `INVENTARIO_URL` y `ENVIOS_URL`. `PEDIDOS_URL` queda como referencia para el cliente y no es necesario para la configuración del servicio. La interfaz local usa las URLs `127.0.0.1` definidas en `cliente/index.html`.

## Solución de problemas

- **401 No autorizado:** asegúrate de que `API_KEY` en `.env` coincide exactamente con el campo `x-api-key` de la interfaz o con el valor autorizado en Swagger. Reinicia los servicios después de modificar `.env`.
- **Servicio no disponible:** confirma que los tres servicios siguen activos y que no hay otro proceso usando los puertos 8001–8003.
- **El navegador no carga el cliente:** verifica que el servidor está activo en el puerto 8000 y abre la URL indicada.
- **Datos inesperados entre demostraciones:** los archivos `.db` persisten localmente. El archivo `.gitignore` excluye estos archivos del repositorio; no los borres si necesitas conservar los datos.
- **PowerShell bloquea la activación del entorno virtual:** puedes omitir la activación y usar `.\venv\Scripts\python.exe` y `.\venv\Scripts\uvicorn.exe` en los comandos.

## Estructura del repositorio

```text
cliente/                 Interfaz web estática
docs/                    Arquitectura SOA y comandos de ejecución
pruebas/                 Espacio para pruebas y evidencias
servicio-inventario/     API de productos y reservas de stock
servicio-envios/         API de logística y guías
servicio-pedidos/        API orquestadora de pedidos
requirements.txt         Dependencias Python
.env.example             Plantilla local de configuración
```

## Alcance

Este repositorio es un prototipo local para fines académicos. Implementa autenticación sencilla por API key, pero no incluye usuarios, roles, HTTPS, despliegue productivo, mensajería asíncrona ni transacciones distribuidas ACID. La coordinación ante fallos usa compensaciones entre servicios y puede dejar pedidos en `REQUIERE_REVISION` si un resultado remoto no puede confirmarse.
