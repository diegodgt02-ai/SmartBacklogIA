# Método de estimación para HU-IA

Entregable 2 del proyecto (propuesta, sección 4). Describe el método con el que la etapa 3 del pipeline estima las historias de usuario (HU) clasificadas como **HU-IA**, cómo se diferencia de la estimación de **HU-Tradicional**, por qué se eligió y cómo se aplica en la aplicación.

Implementación: `app/services/estimation.py` (cálculo puro), `app/agents/prompts.py::ESTIMATOR_INSTRUCTION` (instrucción al agente), `app/agents/runner.py::execute_estimation` (ejecución y trazabilidad).

---

## 1. Problema

Una HU-IA no se estima como una HU determinista por tres razones que la propuesta ya señala:

1. **Dependencia de datos.** El esfuerzo depende de si existen datos, de su calidad y de cuánto hay que etiquetarlos. Un dataset ausente puede multiplicar el trabajo por varios sprints.
2. **Experimentación.** No se sabe de antemano cuántos modelos o prompts hacen falta hasta que se evalúan. El trabajo tiene forma de exploración, no de construcción lineal.
3. **Incertidumbre del resultado.** Aunque la implementación termine en plazo, la precisión o el comportamiento pueden no alcanzar el objetivo. Esa probabilidad no existe en la lógica determinista.

Un único número de story points mezcla estos tres problemas y no permite ver de dónde sale el riesgo. Por eso el método separa factores y expresa la incertidumbre como rango y como spike.

## 2. Revisión de literatura y prácticas

| Fuente | Aporte para este método |
|---|---|
| Amershi, S. et al. (2019). *Software Engineering for Machine Learning: A Case Study*. ICSE-SEIP 2019 (Microsoft). | Describe el flujo de trabajo de ML en equipos Agile y señala tres diferencias frente al software clásico: gestión de datos más difícil, reutilización de modelos que requiere habilidades escasas y modularidad limitada. Sustenta los factores `datos` e `integracion`. |
| Sculley, D. et al. (2015). *Hidden Technical Debt in Machine Learning Systems*. NIPS 2015, pp. 2503–2511 (Google). | Argumenta que los sistemas de ML son baratos de empezar pero caros de mantener, y que gran parte del costo está fuera del modelo (datos, configuración, dependencias). Sustenta el factor `integracion` y la regla de que el costo no termina en el primer entregable. |
| Cohn, M. (2005). *Agile Estimating and Planning*. Prentice Hall. | Base de story points y de estimación relativa en Agile. El método de HU-Tradicional sigue esa tradición. |
| Boehm, B. (1981) y la extensión de McConnell como «cono de incertidumbre» (citada en Cohn, 2005). | La incertidumbre de una estimación es grande al inicio y se reduce con información. Sustenta el rango mín–máx y el factor `incertidumbre`. *Pendiente: verificar la fecha exacta de la cita de McConnell (la fuente consultada da 1997 y 1998).* |
| Estimación de tres puntos (PERT, Malcolm et al., 1959). | Estimar mínimo, esperado y máximo. Sustenta el rango que devuelve la fórmula. |
| Chapman, P. et al. (2000). *CRISP-DM 1.0*. | Fases de comprensión de datos y evaluación antes del despliegue. Sustenta el factor `evaluacion`. |
| Práctica de spikes (XP, Beck, 1999; SAFe). | Investigación con tiempo acotado antes de comprometer una historia. Sustenta el spike obligatorio cuando hay riesgo o datos incompletos. |

> Nota de verificación: las referencias de Amershi (ICSE 2019) y Sculley (NIPS 2015) se comprobaron en fuentes de los autores y de las actas. El título de McConnell citado como *Software Estimation: Demystifying the Black Art* **no se pudo confirmar**; no se usa como cita hasta verificarlo.

### Lo que las fuentes no resuelven

No hay un método estándar y validado de story points para ML. Las prácticas de la industria son adaptaciones (factores de riesgo, spikes, rangos). Por eso este método es una **propuesta justificada y calibrable**, no un valor empírico establecido. La sección 9 explica cómo calibrarlo.

## 3. Método propuesto

### 3.1 Método `ia_factores_v1` (HU-IA)

Cinco factores, cada uno entero de 1 a 5, puntuados por una persona o por el agente:

| Factor | Qué mide | 1 | 3 | 5 | Peso |
|---|---|---|---|---|---|
| `datos` | Disponibilidad y calidad de datos para entrenar o evaluar | Datos listos y limpios | Existen con limpieza parcial | No existen o son de mala calidad | 0.25 |
| `modelo` | Complejidad del modelo o del prompt | Clasificación simple, prompt directo | Varias etapas o extracción compleja | Multimodal, razonamiento largo o varios modelos | 0.25 |
| `evaluacion` | Necesidad de validar con métricas o referencia | Verificación directa | Métrica y conjunto de prueba pequeños | Evaluación humana extensa, sesgo y casos límite | 0.15 |
| `riesgo` | Probabilidad de no alcanzar el resultado esperado | Bajo | Medio: hay precedentes | Alto: precisión o alucinaciones críticas | 0.15 |
| `integracion` | Trabajo tradicional alrededor del modelo (API, UI, seguridad, auditoría) | Mínimo | Moderado | Mucho | 0.20 |

**Paso 1 — puntaje bruto:** `bruto = Σ peso × factor`, con valores entre 1 y 5.

**Paso 2 — escala a Fibonacci:** `índice = redondeo((bruto − 1) × 5 / 4)`, acotado a 0…5 sobre la serie `1, 2, 3, 5, 8, 13`.

**Paso 3 — ajuste por riesgo:** si `riesgo ≥ 4`, el índice sube un nivel (riesgo alto no se queda en la estimación central).

**Paso 4 — rango:** `mín = serie[índice − 1]`, `máx = serie[índice + 1]` (acotados). Es la estimación de tres puntos simplificada: la estimación central es el punto y el rango expresa la incertidumbre.

**Paso 5 — spike:** si `datos ≥ 4` **o** `riesgo ≥ 4`, la HU requiere un spike de **2 puntos** de experimentación con timebox. Esos puntos van aparte de la historia y el spike debe terminar con una decisión (seguir, cambiar de enfoque o descartar).

### 3.2 Método `fibonacci_tradicional_v1` (HU-Tradicional)

Tres factores, cada uno de 1 a 5:

| Factor | Peso |
|---|---|
| `complejidad` (reglas, flujos, casos de error) | 0.40 |
| `esfuerzo` (construcción e integración) | 0.40 |
| `incertidumbre` (claridad del requisito) | 0.20 |

Se aplican los pasos 1 y 2 del método IA. No hay ajuste por riesgo ni spike: una HU tradicional con requisito ambiguo se marca con `incertidumbre` alta, que sube el rango, y se resuelve en refinamiento antes de estimar.

### 3.3 Diferencias clave

| Aspecto | HU-Tradicional | HU-IA |
|---|---|---|
| Factores | 3 | 5 |
| Fuente de incertidumbre | Claridad del requisito | Datos, modelo, evaluación y riesgo de resultado |
| Ajuste por riesgo | No | Sí (sube un nivel si `riesgo ≥ 4`) |
| Spike de experimentación | No | Sí, si `datos ≥ 4` o `riesgo ≥ 4` (2 pts) |
| Rango de estimación | Sí (±1 nivel) | Sí (±1 nivel) |
| Método guardado en la app | `fibonacci_tradicional_v1` | `ia_factores_v1` |

## 4. Ejemplos trabajados

Calculados con el código real (`estimate()` en `app/services/estimation.py`).

**Ejemplo A — Clasificar fraude con IA** (datos parciales, riesgo alto)
- Factores: `datos=4, modelo=3, evaluacion=3, riesgo=4, integracion=2`
- Bruto: 3.20 → índice 3 (5 pts) → riesgo ≥ 4 sube a 8 pts.
- Resultado: **8 pts**, rango 5–13, **spike requerido (2 pts)**.

**Ejemplo B — Extracción de requisitos desde reuniones** (datos listos, modelo moderado)
- Factores: `datos=2, modelo=3, evaluacion=2, riesgo=2, integracion=2`
- Bruto: 2.25 → índice 2 (3 pts), sin ajuste por riesgo (riesgo = 2) → **3 pts**, rango 2–5, sin spike.

**Ejemplo C — Integración con pasarela** (HU-Tradicional)
- Factores: `complejidad=3, esfuerzo=4, incertidumbre=2`
- Bruto: 3.20 → **5 pts**, rango 3–8.

**Ejemplo D — Caso mínimo** (HU-IA con todo en 1)
- Resultado: **1 pt**, rango 1–2, sin spike.

Los ejemplos A y C tienen el mismo bruto (3.20) y obtienen estimaciones distintas solo porque el método de HU-IA aplica el ajuste por riesgo y el spike: esa diferencia es intencional.

## 5. Quién puntúa y quién calcula

- **Un humano o el agente** asigna los factores (enteros 1–5). El agente lo hace con `ESTIMATOR_INSTRUCTION`, que define cada escala y prohíbe calcular puntos.
- **El código** calcula puntos, rango, spike y método. Es reproducible: los mismos factores siempre dan el mismo resultado, y el LLM no puede inventar un número fuera del método.
- **El humano valida** la etapa 3 (HITL). Puede corregir factores o puntos; cada cambio queda en el historial de la HU con el actor y el diff.

## 6. Aplicación en la app

| Dónde | Qué hace |
|---|---|
| `PATCH /api/v1/projects/{key}/stories/{code}` | Acepta `estimate_factors`. El servidor valida, calcula y guarda `estimate_points`, `estimate_method` y `estimate_factors` (con `version`, `range`, `spike`, `raw_score`). Rechaza factores fuera de rango (422), factores faltantes o sobrantes (422) y `estimate_method` que no corresponda al tipo (422). |
| `POST /api/v1/projects/{key}/stories/{code}/agents/estimate` | Ejecuta el estimador (agente ADK o stub). Crea un `AgentRun` con tokens y costo. Registra el diff y la justificación (`estimation_rationale`) en el historial. No cambia el estado de la HU ni aprueba la etapa. |
| Ficha de HU (`/p/{key}/stories/{code}`) | Formulario de edición con selectores 1–5 de los factores del tipo (los del otro tipo se ignoran). Al completarlos, el servidor calcula puntos y método. La sección «Detalles» muestra método, rango, spike y factores. Botón «Estimar con agente» para HU con tipo. |
| Backlog (`/p/{key}/backlog`) | Muestra puntos y una marca «+ spike» en las HU que lo requieren. |
| `services/stories.py::snapshot` | Incluye los campos de estimación para que refinamiento y agentes los vean. |

### Estimación sin factores

`estimate_points` se puede escribir a mano (sin factores). En ese caso no hay rango, spike ni método calculado; el método queda como lo envíe el usuario o vacío. Es el camino para HU ya estimadas por el equipo con otro criterio. Los reportes siguen sumando los puntos.

## 7. Validación HITL

La etapa 3 se aprueba cuando un humano revisa los puntos. La propuesta pide que el sistema bloquee la aprobación si hay HU sin estimar. **Ese bloqueo aún no está implementado** (`services/projects.py::approve_stage` solo exige que la etapa anterior esté aprobada). Queda pendiente de decisión del equipo.

## 8. Limitaciones

- Los pesos (0.25, 0.25, 0.15, 0.15, 0.20 y 0.40, 0.40, 0.20) y la escala lineal son **hipótesis iniciales**, no valores validados.
- La escala Fibonacci tiene pocos niveles por encima de 8; HU muy grandes deberían dividirse, no estimarse como 13.
- El agente puntúa a partir del texto de la HU. Si el texto es pobre, la puntuación es pobre; por eso el factor `incertidumbre` y la nota de `rationale` deben revisarse.
- El spike de 2 puntos es una convención, no una medida de duración real.
- La estimación de HU-IA no reemplaza una prueba de concepto: el spike existe para eso.

## 9. Calibración

Para calibrar el método con datos del equipo:

1. Registrar para cada HU la estimación inicial y el esfuerzo real (puntos quemados o días).
2. Calcular el error relativo por tipo de HU (`real / estimado`).
3. Si HU-IA tiene error sistemáticamente alto, subir el peso del factor más correlacionado con el error o revisar la escala de ese factor.
4. Cambiar pesos o escala **solo** creando `ia_factores_v2` (cambiar `VERSION` y este documento). Las HU ya estimadas conservan su versión de método.

## 10. Trazabilidad

- Cada estimación guarda `estimate_method` y `estimate_factors.version`.
- Cada ejecución del agente queda en `agent_runs` (proveedor, modelo, tokens, costo, resultado).
- Cada cambio queda en `story_events` con el actor (`agent:adk` o el usuario) y el diff.

## 11. Casos de prueba

- Pruebas automáticas: `tests/test_estimation.py` (fórmula, límites, spike, validaciones de API, ruta web, fallo del estimador) y `tests/test_adk_provider.py` (estimador ADK con modelo falso, sin red).
- Caso de prueba end-to-end con reunión simulada y backlog resultante: **pendiente** (entregable 4). Se hará sobre `documents/PAGO/`.
