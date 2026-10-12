# Carpeta de documentos para los agentes IA

Cada proyecto tiene su subcarpeta con la **clave del proyecto** (en mayúsculas):

```
documents/
  PAGO/
    proceso-pago.bpmn      ← diagrama BPMN exportado desde Camunda, Bizagi, draw.io, etc.
    reunion-12-09.pdf      ← transcripción o acta en PDF (con texto, no escaneado)
    requisitos.md          ← notas en Markdown
    reunion.txt            ← texto plano
```

Formatos aceptados: `.pdf`, `.bpmn`, `.md`, `.txt` (máximo 20 MB por archivo).

- Un proyecto solo puede leer su propia subcarpeta.
- El PDF debe tener texto seleccionable. Si es un escaneo, primero pásalo por OCR.
- Los archivos se envían a Google (Gemini) al generar HU: usa solo documentos que tu cuenta esté autorizada a procesar.
- Esta carpeta se monta en el contenedor como solo lectura (`/data/documents`).
- Los documentos reales no se versionan en git (ver `.gitignore`); los de ejemplo sí.
