# Uso por CLI, API Local y Automatización del Seguimiento

## 1. Registrar una jugada desde un archivo JSON

Guarda un JSON **fuera del repositorio** (por ejemplo en `/tmp/mi_jugada.json`):

```json
{
  "label": "Jugada sistemática",
  "mode": "baloto_revancha",
  "start_draw": 2729,
  "draw_count": 3,
  "already_purchased": false,
  "lines": "Combo A: 04 15 23 34 41 | 12\nCombo A: 07 11 19 28 42 | 08"
}
```

- `mode`: `"baloto_revancha"` ($9.000 COP por defecto) o `"baloto"` ($6.000 COP por defecto).
- `price_per_line` *(opcional)*: entero en COP si la tarifa oficial cambia.
- `start_draw` *(opcional)*: sorteo inicial; si se omite, toma `último_sorteo_del_historial + 1`.
- `already_purchased`: `false` deja el registro en estado `cart` (sin contabilizar gasto); `true` lo activa de inmediato (`active`) para evaluar sorteos oficiales.

Registrar e imprimir el JSON resultante sin abrir servidor:

```sh
python3 scripts/baloto.py --prepare /tmp/mi_jugada.json --no-serve
```

Registrar y abrir el tablero web local de inmediato:

```sh
python3 scripts/baloto.py --prepare /tmp/mi_jugada.json --port 8765
```

## 2. Verificación oficial de resultados y premios

Cuando se ejecuta `python3 scripts/baloto.py --check` (o se pulsa **«Sincronizar baloto.com»** en el tablero web):

1. Consulta `https://baloto.com/resultados` para identificar el último sorteo publicado y actualizar `data/historial_baloto.csv` si hay sorteos nuevos.
2. Para cada tiquete en estado `active` cuyos sorteos `#s` ya se hayan jugado (`s <= último_sorteo`):
   - Descarga `https://baloto.com/resultados-baloto/<s>` (y `https://baloto.com/resultados-revancha/<s>` cuando la modalidad incluye Revancha).
   - Extrae las 6 balotas ganadoras y las **8 categorías oficiales de premio por ganador** en COP (`5+SB`, `5`, `4+SB`, `4`, `3+SB`, `3`, `2+SB`, `0 o 1 + SB`).
   - Guarda la tabla oficial en caché dentro de `state.json` y evalúa cada línea una sola vez (operación idempotente).
   - Cuando todos los sorteos del tiquete tienen resultado oficial, el estado pasa automáticamente a `complete`.

## 3. Persistencia, bloqueo y privacidad

- Por defecto, tus tiquetes se guardan en `~/.local/share/baloto/state.json` mediante escritura atómica (`tempfile` + `fsync` + `os.replace`).
- El archivo `.server.lock` evita que dos instancias escriban a la vez en el mismo `--data-dir`. Si un cierre abrupto del sistema deja `.server.lock`, comprueba que el PID dentro del archivo ya no exista antes de borrar `.server.lock` (nunca borres `state.json`).

## 4. Automatización opcional con `cron` (solo si el usuario lo solicita)

Baloto juega **lunes, miércoles y sábado** alrededor de las 11:00 p. m. (hora de Colombia). Si el usuario pide automatizar la revisión sin tener el navegador abierto, puede programar `--check` (por ejemplo, martes/jueves/domingo a las 8:00 a. m.):

```cron
0 8 * * 0,2,4 /usr/bin/env python3 /ruta/a/skills/baloto/scripts/baloto.py --check >> ~/.local/share/baloto/check.log 2>&1
```
