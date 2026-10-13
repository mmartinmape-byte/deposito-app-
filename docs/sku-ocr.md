# Lectura gratuita de SKU

En `/mobile`, la pestaña SKU usa Tesseract.js 6.0.1 para leer la foto en el
navegador del celular. No necesita claves de OpenAI ni Anthropic y no tiene
cobros por lectura. La foto del código no se envía al servidor: solamente el
texto reconocido se envía a `/api/sku/leer` para buscar el producto.

La primera lectura descarga el motor y los datos del OCR. Necesita conexión
para esa descarga y para consultar el catálogo; no es una función totalmente
sin conexión. La biblioteca y el motor usan versiones fijas de jsDelivr; los
datos de idioma usan la distribución predeterminada de Tesseract.js.

Acercar la cámara al código, con buena luz y sin otros textos alrededor.
El OCR funciona mejor con texto impreso. Para códigos manuscritos, revisar
el campo editable y corregirlo antes de pulsar Buscar. Las coincidencias
aproximadas se muestran como sugerencias. Si se reconocen varios códigos del
catálogo, se muestran todos con un aviso.

Las fotos de producto, el stock por palet y la búsqueda manual siguen disponibles.
La carga de una foto de producto sí la envía al servidor para guardarla.

## Publicación

Publicar `app.py`, `templates/mobile.html`, `static/sku-ocr.js` y
`requirements.txt` juntos y recargar la app móvil. El endpoint `/api/sku/leer`
ahora acepta JSON `{"texto": "UBBU75174"}` en vez de una imagen.
La dependencia `anthropic` deja de ser necesaria.

## Verificación

Con las dependencias de Python instaladas:

```sh
python -m unittest discover -s tests -v
```

Las pruebas crean una base SQLite temporal y no usan datos de producción.
