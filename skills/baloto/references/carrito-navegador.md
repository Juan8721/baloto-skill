# Guía de Preparación del Carrito en `baloto.com`

Esta referencia describe cómo cargar jugadas validadas en el carrito web oficial de Baloto usando las herramientas de navegador del agente y el apoyo determinista de [`../scripts/carrito_helper.py`](../scripts/carrito_helper.py).

## Reglas innegociables de seguridad

1. **El usuario inicia sesión:** Pide a la persona que abra `https://baloto.com/` e inicie sesión en su propia ventana del navegador antes de cargar las líneas. Nunca pidas usuario, contraseña, documento ni códigos OTP.
2. **Cero evasión:** No intentes saltar CAPTCHAs, bloqueos geográficos ni validaciones de edad/identidad. Si aparece un control del operador, cede el paso al usuario.
3. **Detenerse antes de pagar:** La tarea del agente termina en la vista de **resumen del carrito**, después de comprobar líneas, modalidad, cantidad de sorteos y monto total en COP. Nunca pulses botones de pago (`Pagar`, `PSE`, `Tarjeta`, `Finalizar compra`).

## Paso 1: Validar y agrupar en boletas de 5 paneles (`A`–`E`)

En la interfaz oficial de Baloto, cada boleta permite hasta **5 paneles (`A`, `B`, `C`, `D`, `E`)**. Si el usuario tiene más de 5 líneas (o las organizó por combos como `Combo A:`, `Combo B:`), `carrito_helper.py` las divide exactamente como espera la web:

```sh
# Desde un tiquete ya guardado en estado 'cart'
python3 scripts/carrito_helper.py --ticket-id <id> --mode plan

# O directamente desde un archivo JSON externo
python3 scripts/carrito_helper.py --input /tmp/jugada.json --mode plan
```

## Paso 2: Recorrido con el navegador del agente

Si el agente cuenta con herramienta de navegador integrada (navegador de Antigravity, Chrome DevTools MCP, Claude in Chrome o Playwright MCP):

1. Confirma con el usuario que su sesión en `baloto.com` ya está abierta.
2. Si son varias boletas o decenas de líneas, puedes inyectar la tarjeta visual de referencia sobre la página actual ejecutando en la consola/evaluador de la pestaña el snippet generado por:
   ```sh
   python3 scripts/carrito_helper.py --ticket-id <id> --mode js
   ```
   Este snippet es puramente local en el DOM (`STOP_BEFORE_CHECKOUT = true`), expone `window.__balotoCartPlan` con los paneles exactos y muestra un recuadro flotante de verificación sin leer cookies ni tocar formularios sensibles.
3. Para cada boleta del plan:
   - Selecciona apuesta **Manual**.
   - En cada panel (`A`..`E`), marca las **5 balotas (`1..43`)** y la **Superbalota (`1..16`)**.
   - Activa o desactiva **Revancha** según `mode` (`Baloto + Revancha` vs. `Solo Baloto`).
   - Selecciona la cantidad de **sorteos consecutivos (`draw_count`)**.
   - Añade la boleta al carrito.
4. Abre el carrito y comprueba que:
   - La cantidad total de líneas coincida con `total_lines`.
   - El monto total en pantalla coincida exactamente con `total_cost_formatted` (p. ej. `$45.000`).
5. Informa al usuario que el carrito está listo para su revisión y pago manual.

## Paso 3: Confirmar el tiquete tras la compra

Una vez que la persona confirme que realizó el pago (y verifique en su comprobante el número exacto de sorteo inicial `#XXXX`):

- Pulsa **«Confirmar que ya compré el tiquete»** en el tablero local (`http://127.0.0.1:8765/seguimiento/<id>`), o confírmalo vía API/CLI para activar el seguimiento automático de resultados oficiales.
