# Smart Product Backlog — MVP

Plataforma de gestión de historias de usuario (HU) para el Smart Product Backlog del curso.
Los agentes ADK hacen la IA (generar, clasificar, estimar, refinar) y se conectan a esta API.
**Este MVP no llama a ningún LLM.**

Incluye:

- Login, roles por proyecto y administración de usuarios y API keys.
- Proyectos, épicas, HU con estado, asignación, auditoría y reportes (CSV/JSON).
- Validación HITL obligatoria por etapa (1–5): no se aprueba una etapa sin aprobar la anterior.
- API para que ADK cree proyectos, usuarios, HU y asignaciones, y para el ciclo de refinamiento.
- UI web: dashboard con pipeline, backlog jerárquico, tablero Kanban, detalle de HU, reportes y miembros.

---

## 1. Levantar con Docker Compose

Requisito: Docker con el plugin `compose`.

```bash
cd "Proyecto final/prueba_mcp"
cp .env.example .env                              # opcional: cambia SESSION_SECRET
docker compose up --build -d                      # levanta Postgres y la app
docker compose exec app python scripts/seed.py    # una sola vez: admin + proyecto SPB de demo
```

| Servicio | Dirección | Notas |
|---|---|---|
| App (UI y API) | http://localhost:8000 | `/login` para la UI, `/docs` para Swagger |
| Postgres | `localhost:5433` | usuario `spb`, clave `spb`, base `spb`. Puerto 5433 para no chocar con un Postgres local en 5432 |

Al arrancar, el contenedor `app` aplica las migraciones (`alembic upgrade head`).

Comandos útiles:

```bash
docker compose ps                 # estado de los servicios
docker compose logs -f app        # logs de la API
docker compose up --build -d app  # reconstruir la app tras cambios de código
docker compose down               # parar; los datos quedan en el volumen pgdata
docker compose down -v            # parar y borrar la base
```

Sin Docker:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
export DATABASE_URL=postgresql+psycopg://spb:spb@localhost:5433/spb
.venv/bin/alembic upgrade head
.venv/bin/python scripts/seed.py
.venv/bin/uvicorn app.main:app --reload
```

### Usuarios de prueba (creados por el seed)

| Correo | Rol | Contraseña |
|---|---|---|
| `admin@spb.local` | Administrador del sistema (ve todos los proyectos y `/admin`) | `cambiar-me-ya` |
| `po@spb.local` | Product owner de SPB | `demo-spb-2026` |
| `sm@spb.local` | Scrum master de SPB | `demo-spb-2026` |
| `dev@spb.local` | Developer de SPB | `demo-spb-2026` |
| `stake@spb.local` | Stakeholder de SPB (solo lectura) | `demo-spb-2026` |

El admin se configura con `SEED_ADMIN_EMAIL` y `SEED_ADMIN_PASSWORD`. Cambia todas las contraseñas antes de exponer el servicio.

---

## 2. Documentación de la API

- **Swagger interactivo:** http://localhost:8000/docs
- **Esquema OpenAPI:** http://localhost:8000/openapi.json

Se autentica de dos formas:

| Quién | Cómo | Dónde |
|---|---|---|
| Humano (desde Swagger o terminal) | `POST /api/v1/auth/login` devuelve cookie de sesión | Swagger guarda la cookie al hacer login |
| Agente ADK (servicio) | Header `X-API-Key: spb_...` | Botón **Authorize** en Swagger, o el header en cada llamada |

Para crear una API key: entra con el admin, luego `POST /api/v1/admin/api-keys` con `{"name": "adk-clasificacion"}`. La clave en claro se devuelve **una sola vez**. También se puede generar en la pantalla `/admin`.

### Roles y permisos por proyecto

| Rol | Puede |
|---|---|
| Admin del sistema | Todo; crear usuarios y API keys |
| `product_owner` | Todo en el proyecto, incluida la aprobación HITL |
| `scrum_master` | Crear y editar HU, asignar, mover estados, pedir refinamiento |
| `developer` | Mover HU entre `lista` → `en_progreso` → `en_revision` (y regresar) |
| `stakeholder` | Solo lectura |
| Servicio ADK (API key) | Ingesta, crear HU, asignar, mover estados, refinamiento. No aprueba HITL |

### Estados de una HU

Flujo principal: `borrador` → `en_refinamiento` → `refinada` → `lista` → `en_progreso` → `en_revision` → `hecha`.

Salidas permitidas (la matriz completa está en `app/services/workflow.py`):

| Desde | Puede pasar a |
|---|---|
| `borrador` | `en_refinamiento`, `descartada` |
| `en_refinamiento` | `refinada`, `borrador`, `descartada` |
| `refinada` | `lista`, `en_refinamiento`, `descartada` |
| `lista` | `en_progreso`, `refinada`, `descartada` |
| `en_progreso` | `en_revision`, `lista` |
| `en_revision` | `hecha`, `en_progreso` |
| `descartada` | `borrador` |
| `hecha` | (final) |

Transición fuera de la matriz → `422`. Transición válida sin permiso para el rol → `403`.
Matriz completa en `app/services/workflow.py`.

---

## 3. Contrato para agentes ADK

Base URL: `http://localhost:8000`. Todas las llamadas llevan `X-API-Key` y `Content-Type: application/json`.

### 3.1 Crear proyecto, usuarios, miembros, épicas, HU y asignaciones

**Endpoint:** `POST /api/v1/ingest/projects`
**Respuesta:** `201 Created`

Es **idempotente**: si el `key` del proyecto ya existe, lo actualiza; si una HU trae un `external_id` que ya existe en el proyecto, la actualiza en vez de duplicarla. Por eso ADK puede reenviar el mismo backlog sin crear duplicados.

**Request:**

```json
{
  "key": "PAGO",
  "name": "Pagos en línea",
  "description": "Backlog generado desde reuniones con stakeholders",
  "users": [
    {"email": "ana.po@empresa.co", "name": "Ana Pérez", "password": "clave-ana-2026"},
    {"email": "luis.dev@empresa.co", "name": "Luis Gómez", "password": "clave-luis-2026"}
  ],
  "members": [
    {"email": "ana.po@empresa.co", "role": "product_owner"},
    {"email": "luis.dev@empresa.co", "role": "developer"}
  ],
  "epics": [
    {
      "title": "Checkout",
      "description": "Flujo de pago",
      "stories": [
        {
          "external_id": "reunion-12-09-hu-1",
          "title": "Pagar con tarjeta de crédito",
          "role": "cliente",
          "need": "pagar con tarjeta",
          "benefit": "completar la compra",
          "acceptance_criteria": "- [ ] Muestra error si la tarjeta es rechazada\n- [ ] Permite reintentar hasta 3 veces",
          "type": "HU_TRADICIONAL",
          "type_justification": "Integración con pasarela",
          "estimate_points": 5,
          "priority_rank": 1,
          "source_ref": "Reunión 12/09, min 4:10",
          "assignee_email": "luis.dev@empresa.co"
        }
      ]
    }
  ],
  "stories": [
    {
      "external_id": "reunion-12-09-hu-2",
      "title": "Clasificar fraude con IA",
      "type": "HU_IA",
      "source_ref": "Reunión 12/09, min 17:32"
    }
  ]
}
```

**Campos:**

| Campo | Obligatorio | Descripción |
|---|---|---|
| `key` | sí | 2–10 caracteres, mayúsculas y dígitos, empieza con letra. Ej. `PAGO` |
| `name` | sí | Nombre del proyecto |
| `users[]` | no | Usuarios a crear si no existen. `password` opcional (mínimo 8). Sin contraseña no pueden iniciar sesión |
| `members[]` | no | `email` debe existir en `users` o ya en el sistema. `role`: `product_owner`, `scrum_master`, `developer`, `stakeholder` |
| `epics[].title` | sí (si hay épicas) | Se crea si no existe; se identifica por título |
| `stories[].title` | sí | Mínimo 3 caracteres |
| `stories[].external_id` | recomendado | Id estable del agente. Clave de idempotencia |
| `stories[].type` | no | `HU_IA` o `HU_TRADICIONAL` |
| `stories[].assignee_email` | no | Correo de un miembro del proyecto. Si no es miembro, responde `422` |
| `stories[]` (raíz) | no | HU sin épica |

Campos opcionales de HU: `role`, `need`, `benefit`, `acceptance_criteria` (**markdown**, máx. 20 000 caracteres; ver [sección 3.5), `type_justification`, `estimate_points` (número), `estimate_method`, `estimate_factors` (objeto), `priority_rank` (entero), `priority_method`, `source_ref`.

**Response:**

```json
{
  "project": "PAGO",
  "project_created": false,
  "users_created": 2,
  "stories_created": 2,
  "stories_updated": 0
}
```

Respuestas de error: `401` (API key inválida), `422` (un miembro no existe o un campo no cumple validación).

#### Crear o actualizar HU sin reingestar todo

- `POST /api/v1/projects/{key}/stories` crea una HU. Usa el mismo cuerpo que una HU de `ingest` (con `epic` por título si se quiere).
- `POST /api/v1/projects/{key}/stories:bulk` body `{"stories": [ ... ]}` crea o actualiza en lote por `external_id`. Respuesta: `{"created": n, "updated": m}`.
- `PATCH /api/v1/projects/{key}/stories/{code}` actualiza campos. Puede enviar `expected_version` para evitar sobrescribir cambios ajenos (`409` si no coincide).

#### Asignar una HU

**Endpoint:** `POST /api/v1/projects/{key}/stories/{code}/assign`

```json
{"assignee_email": "luis.dev@empresa.co"}
```

- Para quitar la asignación: `{"assignee_email": ""}`.
- Un humano también puede usar `{"user_id": 3}`.
- El correo debe ser miembro del proyecto; si no, `422`.

Respuesta: la HU completa, con `assignee: {id, name, email}`.

### 3.2 Refinar una HU

El ciclo tiene cuatro pasos: solicitar, tomar, resolver y consultar.

#### Paso 1 — Solicitar refinamiento

**Endpoint:** `POST /api/v1/projects/{key}/stories/{code}/status`

```json
{"status": "en_refinamiento", "expected_version": 2}
```

`expected_version` es opcional. Al entrar a `en_refinamiento` se crea una solicitud `pending`. Si la HU se mueve a otro estado mientras tanto, la solicitud pasa a `cancelled`.

Si hay un webhook configurado (`ADK_WEBHOOK_URL`), el sistema hace `POST` a esa URL con `{id, project_key, status, story, claimed_by, created_at}`, donde `story` es el mismo `input` del paso 2.

#### Paso 2 — Consultar la cola

**Endpoint:** `GET /api/v1/refinements?status=pending&project_key=PAGO`

Parámetros: `status` (`pending`, `in_progress`, `completed`, `cancelled`; por defecto `pending`) y `project_key` opcional.

**Response:**

```json
[
  {
    "id": 3,
    "story_code": "PAGO-2",
    "project_key": "PAGO",
    "status": "pending",
    "claimed_by": null,
    "claimed_at": null,
    "completed_at": null,
    "created_at": "2026-10-10T04:33:40.929599+00:00",
    "input": {
      "code": "PAGO-2",
      "epic": "Checkout",
      "title": "Pagar con tarjeta de crédito",
      "role": "cliente",
      "need": "pagar con tarjeta",
      "benefit": "completar la compra",
      "acceptance_criteria": "- [ ] Muestra error si la tarjeta es rechazada",
      "type": "HU_TRADICIONAL",
      "type_justification": "Integración con pasarela",
      "version": 2
    },
    "result": null,
    "error": null
  }
]
```

El agente debe usar `input` como contexto para el LLM. Guarda `input.version`: ese valor va en el paso 4.

#### Paso 3 — Tomar la solicitud

**Endpoint:** `POST /api/v1/refinements/{id}/claim`

Sin cuerpo. Pasa la solicitud a `in_progress` y la asocia a la API key que la toma. Si ya fue tomada por otro agente, responde `409`. Esto evita que dos agentes procesen la misma HU.

#### Paso 4 — Devolver el resultado

**Endpoint:** `POST /api/v1/refinements/{id}/result`

**Request:**

```json
{
  "expected_version": 2,
  "title": "Pagar con tarjeta de crédito y débito",
  "role": "cliente",
  "need": "pagar mi compra con tarjeta de crédito o débito",
  "benefit": "completar la compra sin salir del sitio",
  "acceptance_criteria": "- [ ] Muestra error claro si la tarjeta es rechazada\n- [ ] Permite reintentar hasta 3 veces\n- [ ] Confirma el pago por correo",
  "notes": "Se separó el caso débito; se añadió el criterio de reintento."
}
```

| Campo | Obligatorio | Descripción |
|---|---|---|
| `expected_version` | sí | El `input.version` que se leyó en el paso 2 |
| `title`, `role`, `need`, `benefit`, `acceptance_criteria` | no | Solo se actualizan los campos enviados (`null` o ausente = sin cambio). `acceptance_criteria` es un string markdown; una lista responde `422` |
| `notes` | no | Se guarda en el historial de la HU |

Efecto: la HU pasa a `refinada`, su versión sube, la solicitud pasa a `completed` y queda registrado en el historial qué cambió. Un humano la revisa y la pasa a `lista`.

**Response:** la solicitud con `status: "completed"` y `result` con el cuerpo enviado.

**Errores:**

| Código | Cuándo |
|---|---|
| `409` | La solicitud no está `in_progress` (no se tomó con claim, o ya se resolvió) |
| `409` | `expected_version` no coincide: la HU cambió. Vuelve a leer la HU y reintenta |
| `409` | La HU ya no está en `en_refinamiento` |
| `401` | API key inválida |

### 3.2.1 Estimar una HU (etapa 3)

Método completo y justificación: [`docs/metodo-estimacion-hu-ia.md`](docs/metodo-estimacion-hu-ia.md).

- **Por factores (recomendado):** `PATCH /api/v1/projects/{key}/stories/{code}` con `estimate_factors`. El servidor calcula `estimate_points`, `estimate_method` y el bloque completo (rango, spike, puntaje bruto). Factores válidos: enteros de 1 a 5.
  - HU_IA: `{"datos": 4, "modelo": 3, "evaluacion": 3, "riesgo": 4, "integracion": 2}`
  - HU_TRADICIONAL: `{"complejidad": 3, "esfuerzo": 4, "incertidumbre": 2}`
  - Respuesta de error `422` si faltan o sobran factores, si un valor está fuera de 1–5, si la HU no tiene tipo, o si `estimate_method` no corresponde al tipo.
- **Con agente:** `POST /api/v1/projects/{key}/stories/{code}/agents/estimate` (rol de escritura o API key de servicio). Responde `202` con un `AgentRun`. El agente puntúa factores; el servidor calcula los puntos. No cambia el estado de la HU.
- **Manual:** `estimate_points` sin factores sigue funcionando.

### 3.3 Otros endpoints útiles para ADK

| Método y ruta | Uso |
|---|---|
| `GET /api/v1/projects/{key}/stories?status=&type=&epic_id=&assignee_id=` | Listar HU con filtros |
| `GET /api/v1/projects/{key}/stories/{code}` | Detalle de una HU |
| `GET /api/v1/projects/{key}/stories/{code}/events` | Historial de cambios |
| `POST /api/v1/projects/{key}/stories/{code}/status` | Mover de estado (valida la matriz) |
| `GET /api/v1/projects/{key}/pipeline` | Estado de las etapas HITL |
| `GET /api/v1/projects/{key}/reports/summary` | Resumen por estado, tipo, épica y asignado |
| `GET /api/v1/projects/{key}/export?format=csv\|json` | Exportar backlog |
| `GET /api/v1/refinements/{id}` | Detalle de una solicitud de refinamiento |

La lista completa con todos los campos está en Swagger (`/docs`).

### 3.4 Ejemplo completo con curl

```bash
B=http://localhost:8000
KEY="spb_...."   # creada en /admin o con POST /api/v1/admin/api-keys
J='Content-Type: application/json'

# 1. Ingesta
curl -s -H "X-API-Key: $KEY" -H "$J" -d @ingesta.json $B/api/v1/ingest/projects

# 2. Solicitar refinamiento de PAGO-2 (versión actual 2)
curl -s -H "X-API-Key: $KEY" -H "$J" -d '{"status":"en_refinamiento"}' $B/api/v1/projects/PAGO/stories/PAGO-2/status

# 3. Ver la cola y obtener el id
curl -s -H "X-API-Key: $KEY" "$B/api/v1/refinements?status=pending&project_key=PAGO"

# 4. Tomar la solicitud
curl -s -X POST -H "X-API-Key: $KEY" $B/api/v1/refinements/3/claim

# 5. Devolver el resultado
curl -s -H "X-API-Key: $KEY" -H "$J" -d @resultado.json $B/api/v1/refinements/3/result
```

Respuestas que obtuvimos al probar este flujo:

- Paso 2: `{"status": "en_refinamiento", "version": 2}`.
- Paso 5 con versión correcta: `status: "completed"`. La HU queda en `refinada` con versión 3.
- Paso 5 repetido con la versión vieja: `409`.

---

### 3.5 Criterios de aceptación en markdown

`acceptance_criteria` es un **string en markdown** en todas las entradas y salidas (ingesta, HU, refinamiento, export CSV y la UI). Formato recomendado:

```markdown
- [ ] El cliente ve el motivo del rechazo en lenguaje claro
- [ ] Se permiten hasta 3 reintentos por hora

## Escenario de error
- **Dado** una tarjeta rechazada, **cuando** el cliente confirma, **entonces** ve el error
```

La UI lo muestra renderizado con markdown **sin HTML**: si el texto trae etiquetas (`<script>`), se muestran escapadas. Una lista JSON responde `422`.

---

## 4. Agentes IA embebidos (ADK + Gemini)

Los agentes viven dentro del backend, en `app/agents/`. Sirven para **generar HU desde documentos** y **refinar HU**. No necesitan MCP ni un servicio aparte.

```
app/agents/
  ports.py            contrato: DTOs (GeneratedStory, RefinementOutput) y protocolo AgentProvider
  registry.py         elige el proveedor según AGENT_PROVIDER: none | stub | adk
  runner.py           orquesta: crea agent_runs, persiste HU con app/services, aplica reglas HITL
  prompts.py          instrucciones del agente (formato de HU, criterios en markdown)
  providers/
    stub.py           determinista, sin LLM (demo y pruebas)
    adk.py            Google ADK: LlmAgent + Runner + output_schema → Gemini
app/services/documents/   lectura de PDF, BPMN, MD y TXT desde carpetas locales
app/routers/agents.py     endpoints /api/v1/.../agents/*
```

**Separación de responsabilidades:** el proveedor solo devuelve datos validados por Pydantic. **No escribe en la base** y no tiene herramientas de escritura. Quien crea o actualiza HU es `AgentRunner`, con los mismos servicios que usa la API (permisos, auditoría, versiones).

**Reglas que siempre se cumplen, sin importar el modelo:**

- La generación solo crea HU en `borrador`. Reprocesar el mismo documento actualiza (no duplica): cada HU tiene un `external_id` determinista.
- El refinamiento pasa por el mismo flujo que un ADK externo (`claim` → `submit_result`) y termina en `refinada`. El agente **nunca aprueba etapas HITL ni mueve HU a `lista`**.
- Cada ejecución queda en `agent_runs` (estado, error, tokens, costo). Un fallo no deja HU a medias: la generación es una sola transacción.
- Los cambios del agente quedan en el historial de la HU con actor `agent:<proveedor>`.
- Solo product owner, scrum master y administradores pueden lanzar agentes. Developers y stakeholders reciben `403`.

### 4.1 Endpoints

| Método y ruta | Qué hace | Respuesta |
|---|---|---|
| `GET /api/v1/agents/status` | Proveedor, modelo y si está configurado | `200` |
| `GET /api/v1/projects/{key}/agents/documents` | Archivos de `documents/<KEY>/` | `200` |
| `POST /api/v1/projects/{key}/agents/generate-stories` | Genera HU. Body: `{"filename": "proceso.bpmn"}` **o** `{"document_text": "...", "source_name": "reunion"}`; opcional `instructions` | `202` con el run |
| `POST /api/v1/projects/{key}/agents/generate-stories/upload` | Igual, pero subiendo el archivo (`multipart`) | `202` |
| `POST /api/v1/projects/{key}/stories/{code}/agents/refine` | Refina la HU (la pasa a `en_refinamiento` si hace falta). Body opcional `{"instructions": "..."}` | `202` |
| `GET /api/v1/projects/{key}/agents/runs` | Últimas 100 ejecuciones del proyecto | `200` |
| `GET /api/v1/agents/runs/{id}` | Detalle de una ejecución | `200` |

Errores: `503` si el proveedor no está configurado (el resto de la API sigue funcionando); `413` si el documento supera `AGENT_MAX_DOC_CHARS`; `422` si el archivo no se puede leer (PDF escaneado, BPMN con entidades externas, formato no soportado, ruta inválida); `404` si el archivo no existe en la carpeta del proyecto.

Ejemplo, generar desde un BPMN de la carpeta:

```bash
curl -s -H "Content-Type: application/json" -b cookies.txt \
  -d '{"filename": "proceso-pago.bpmn"}' \
  http://localhost:8000/api/v1/projects/PAGO/agents/generate-stories
# → 202 con {"id": 7, "status": "queued", ...}

curl -s -b cookies.txt http://localhost:8000/api/v1/agents/runs/7
# → {"status": "succeeded", "result": {"created": ["PAGO-4", "PAGO-5"], "updated": [], ...}, "tokens_in": ..., "cost_usd": ...}
```

Ejemplo, refinar una HU con el agente:

```bash
curl -s -H "Content-Type: application/json" -b cookies.txt \
  -d '{"instructions": "Agrega casos de error de la pasarela"}' \
  http://localhost:8000/api/v1/projects/PAGO/stories/PAGO-2/agents/refine
```

### 4.2 Carpetas de documentos

Cada proyecto tiene una subcarpeta con su **clave en mayúsculas**, dentro de `documents/` (en Docker se monta en `/data/documents`, solo lectura):

```
documents/
  PAGO/
    proceso-pago.bpmn    diagrama BPMN (Camunda, Bizagi, draw.io, ...)
    reunion-12-09.pdf    PDF con texto seleccionable (no escaneado)
    requisitos.md
    reunion.txt
```

| Formato | Cómo se lee |
|---|---|
| `.bpmn` | Se convierte en un resumen: procesos, participantes (carriles), actividades con su tipo y documentación, decisiones y flujo. Se parsea con `defusedxml`, que rechaza DTD y entidades externas |
| `.pdf` | Texto por página con `pypdf`. Un PDF sin texto (escaneo) responde `422`: hace falta OCR |
| `.md`, `.txt` | UTF-8 tal cual |

Un proyecto solo ve su subcarpeta. Las rutas con `..`, nombres con carpetas y symlinks que salen de la carpeta se rechazan. Los documentos se envían a Google al generar HU: usa solo los que tu cuenta esté autorizada a procesar.

### 4.3 Activar Gemini (paso a paso)

El código está listo; los pasos con tu cuenta los haces tú:

1. **Clave de API.** Crea una API key en Google AI Studio (o en la consola de Google Cloud de tu cuenta empresarial, con acceso a la Gemini API). Guárdala solo en `.env`: no la pegues en chats, tickets ni commits. Si alguna vez la compartiste, bórrala y crea otra.
2. **Configura `.env`** (copia de `.env.example`):
   ```
   AGENT_PROVIDER=adk
   GOOGLE_API_KEY=tu-clave
   AGENT_MODEL=gemini-3.1-pro-preview
   ```
3. **Recrea el contenedor** para que lea el `.env` (un contenedor ya corriendo no recibe cambios de `.env`):
   ```bash
   docker compose up -d app
   ```
4. **Confirma qué modelos tiene tu cuenta** y ajusta `AGENT_MODEL` al que aparezca:
   ```bash
   docker compose exec app python scripts/try_agent.py --list-models
   ```
5. **Prueba directa**, sin pasar por la API. No guarda nada y gasta cuota de Gemini:
   ```bash
   docker compose exec app python scripts/try_agent.py proceso-pago.bpmn --project PAGO --refine-sample
   ```
6. **Guarda las HU** en el proyecto, con el mismo flujo que la API:
   ```bash
   docker compose exec app python scripts/try_agent.py proceso-pago.bpmn --project PAGO --save
   ```
   Las HU quedan en `borrador`. Reprocesar el mismo documento actualiza las HU existentes y no duplica.

Para probar sin Gemini usa `AGENT_PROVIDER=stub`: mismo flujo, con un proveedor determinista que convierte actividades de BPMN y viñetas en HU. Sirve para revisar el flujo sin gastar cuota.

Verificado: con `gemini-3.1-pro-preview` el comando de la prueba directa generó HU de un BPMN real (con tokens contados) y refinó una HU de muestra. Los tests cubren además el adaptador ADK completo (LlmAgent, Runner, sesión, `output_schema`, conteo de tokens) con un modelo falso que implementa `BaseLlm`, sin red.

### 4.4 Script `try_agent.py`

| Uso | Qué hace |
|---|---|
| `--list-models` | Lista los modelos que tu clave puede usar |
| `proceso-pago.bpmn --project PAGO` | Busca el archivo en `documents/PAGO/` y muestra las HU propuestas |
| `documents/PAGO/proceso-pago.bpmn --project PAGO` | También acepta la ruta con la carpeta del proyecto |
| `... --refine-sample` | Además refina una HU de ejemplo. No la guarda |
| `... --save` | Guarda las HU en el proyecto (en `borrador`). Requiere que el proyecto exista |

Dentro del contenedor, la carpeta de documentos está en `/data/documents`. El script resuelve los nombres a partir de `AGENT_DOCS_DIR`, así que no necesitas escribir esa ruta.

### 4.5 Problemas frecuentes

| Mensaje | Causa y solución |
|---|---|
| `no configuration file provided: not found` | Ejecutaste `docker compose` fuera de la carpeta del proyecto. Entra a la carpeta donde está `docker-compose.yml` |
| `Falta GOOGLE_API_KEY` | La clave no está en `.env`, o no recreaste el contenedor (`docker compose up -d app`) |
| `Agentes desactivados: define AGENT_PROVIDER=...` | `AGENT_PROVIDER` está en `none`. Ponlo en `adk` o `stub` |
| `404 NOT_FOUND ... no longer available to new users` | El modelo de `AGENT_MODEL` ya no está disponible para tu cuenta. Usa `--list-models` y elige otro |
| `400 INVALID_ARGUMENT` | Petición que el modelo rechaza. Las restricciones de longitud ya no se envían a Gemini; si vuelve a aparecer, revisa el modelo configurado |
| `No existe el documento X en la carpeta del proyecto` | El archivo no está en `documents/<CLAVE>/`. Revisa el nombre y la clave del proyecto |
| `El proyecto X no existe` | Con `--save`, crea el proyecto antes (ingesta o UI) |
| `503` en la API o en la UI | El proveedor no está configurado. Revisa `AGENT_PROVIDER` y `GOOGLE_API_KEY` |
| `422` al generar desde un PDF | El PDF no tiene texto (es un escaneo). Pásalo por OCR |

---

## 5. Configuración

Variables (`.env` o entorno del contenedor):

| Variable | Por defecto | Uso |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://spb:spb@localhost:5432/spb` | Conexión a Postgres. En Docker la app usa `db:5432` |
| `SESSION_SECRET` | `cambiar-en-produccion` | Firma de la cookie de sesión. Cámbiala |
| `ADK_WEBHOOK_URL` | vacío | Si se define, recibe un `POST` al crear cada solicitud de refinamiento |
| `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` | `admin@spb.local` / `cambiar-me-ya` | Solo para `scripts/seed.py` |
| `AGENT_PROVIDER` | `none` | `none` (desactivado), `stub` (sin LLM) o `adk` (Gemini) |
| `GOOGLE_API_KEY` | vacío | Clave de Gemini API. Solo en `.env`, nunca en el repo |
| `AGENT_MODEL` | `gemini-3.1-pro-preview` | Id del modelo Gemini (confirmar con `--list-models`) |
| `AGENT_EXECUTION` | `background` | `background` (segundo plano) o `inline` (espera el resultado) |
| `AGENT_AUTO_REFINE` | `false` | `true`: al pasar una HU a `en_refinamiento`, el agente interno la refina |
| `AGENT_MAX_DOC_CHARS` | `200000` | Tamaño máximo del documento en caracteres (`413` si se excede) |
| `AGENT_DOCS_DIR` | `documents` | Carpeta raíz de documentos por proyecto (en Docker: `/data/documents`) |
| `AGENT_PRICE_IN_PER_1M` / `AGENT_PRICE_OUT_PER_1M` | vacío | Precio por millón de tokens; si no se definen, `cost_usd` queda vacío |

---

## 6. Tests

```bash
.venv/bin/python -m pytest                       # SQLite temporal
TEST_DATABASE_URL=postgresql+psycopg://spb:spb@localhost:5433/spb_test .venv/bin/python -m pytest
```

Para un caso: `pytest tests/test_api.py::test_refinement_cycle`.
Con Postgres, crea antes la base `spb_test` (`docker compose exec db createdb -U spb spb_test`). Los tests borran sus datos después de cada caso; no uses la base de desarrollo.

Cubren: ingesta idempotente, ciclo de refinamiento con control de versión, permisos por rol, validación HITL, asignación por correo desde ADK, reportes y exportación, y las pantallas principales.

---

## 7. Estructura

```
app/
  main.py            app FastAPI, sesiones, errores (JSON en /api, HTML en la web)
  models.py          modelo de datos (SQLAlchemy 2)
  schemas.py         contratos de entrada y salida
  services/          reglas de negocio: workflow, stories, refinement, projects, access, audit
  services/documents/  lectura de PDF, BPMN, MD y TXT desde carpetas por proyecto
  routers/           API JSON (auth, admin, projects, stories, adk, agents) y web (ui.py)
  agents/            agentes IA: puertos, runner, proveedores stub y adk (ver sección 4)
  templates/         Jinja2 (UI)
  static/            app.css (tema claro/oscuro) y app.js (tema, toasts, Kanban)
alembic/             migraciones
documents/           carpetas de documentos por proyecto (PAGO/ tiene ejemplos)
scripts/seed.py      datos de demo
scripts/try_agent.py prueba directa de Gemini sin la API
tests/               pytest (API y UI)
```

---

## 8. Limitaciones del MVP

- Sin transcripción de audio: las reuniones se entregan como texto (MD/TXT) o PDF con texto.
- Gemini: la integración está probada con un modelo falso; la llamada real requiere tu `GOOGLE_API_KEY`.
- PDF escaneados (sin texto) no se procesan: falta OCR.
- El agente interno corre dentro del mismo proceso (en `background`). Si la app se reinicia a mitad de una ejecución, esa ejecución queda en `running`; un worker separado es el siguiente paso si hace falta.
- Estimación y priorización son campos enviados por ADK o manuales; no hay cálculo automático.
- No hay exportación directa a Jira o Trello; el CSV/JSON sirve de puente.
- Una contraseña enviada en `users[]` se guarda sólo si ese usuario es nuevo; no se cambia en ingestas posteriores. Para cambiarla hace falta una pantalla o endpoint de administración que aún no existe.
- En desarrollo la app crea las tablas al arrancar (`create_all`). En despliegue usa `alembic upgrade head`.
- Los estilos son CSS propio (sin framework).
