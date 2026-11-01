# Uso por CLI, API Local y Automatización del Seguimiento

*(En Windows, reemplaza `python3` por `python` o `py -3` si el alias `python3` no está configurado).*

## 1. Registrar una jugada por `stdin` o desde un archivo JSON

Puedes enviar el JSON directamente por `stdin` (`--prepare -`) para no dejar archivos con tus jugadas en el directorio de trabajo, o leerlo desde una ruta fuera del repositorio (`/tmp/mi_jugada.json`):

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

Registrar por `stdin` e imprimir el JSON resultante sin abrir servidor:

```sh
python3 scripts/baloto.py --prepare - --no-serve < /tmp/mi_jugada.json
```

Confirmar desde CLI un tiquete en carrito una vez comprado:

```sh
python3 scripts/baloto.py --confirm <ticket_id> --start-draw 2729
```

Abrir el tablero web local:

```sh
python3 scripts/baloto.py --port 8765
```

## 2. Verificación oficial de resultados y premios

Cuando se ejecuta `python3 scripts/baloto.py --check` (o se pulsa **«Sincronizar baloto.com»** en el tablero web):

1. Consulta `https://baloto.com/resultados` (restringiendo redirecciones únicamente a `baloto.com` / `www.baloto.com` sobre HTTPS y un máximo de 2 MB) para identificar el último sorteo publicado y actualizar `data/historial_baloto.csv` si hay sorteos nuevos.
2. Para cada tiquete en estado `active` cuyos sorteos `#s` ya se hayan jugado (`s <= último_sorteo`):
   - Descarga `https://baloto.com/resultados-baloto/<s>` (y `https://baloto.com/resultados-revancha/<s>` cuando la modalidad incluye Revancha) fuera del candado de lectura del estado.
   - Verifica que el escrutinio oficial ya esté liquidado (categoría de reembolso fijo `0 o 1 + SB` > `$0`). Si recién terminó la transmisión y la tabla aún aparece en `$0`, el sorteo permanece pendiente hasta la siguiente consulta.
   - Extrae las 6 balotas ganadoras y las **8 categorías oficiales de premio por ganador** en COP (`5+SB`, `5`, `4+SB`, `4`, `3+SB`, `3`, `2+SB`, `0 o 1 + SB`).
   - Guarda la tabla oficial en caché dentro de `state.json` y evalúa cada línea de forma idempotente.
   - Cuando todos los sorteos del tiquete tienen resultado oficial liquidado, el estado pasa automáticamente a `complete`.

## 3. Persistencia, bloqueo transaccional y privacidad

- Por defecto, tus tiquetes se guardan fuera del repositorio en `~/.local/share/baloto/state.json` (o `%LOCALAPPDATA%\baloto\state.json` en instalaciones nuevas de Windows, personalizable con `BALOTO_DATA_DIR` o `--data-dir`) con permisos `0700` en el directorio y `0600` en `state.json`.
- Las escrituras concurrentes entre el servidor web y comandos puntuales (`--prepare --no-serve`, `--confirm`, `--check`) se coordinan mediante bloqueo de archivo del sistema operativo (`.store.lock` con `fcntl`/`msvcrt`) y recarga automática si `state.json` cambió en disco.
- El bloqueo `.server.lock` impide levantar dos servidores HTTP simultáneos sobre el mismo `--data-dir` y se libera automáticamente por el sistema operativo incluso tras un cierre inesperado del proceso.

## 4. Automatización opcional con `cron` (solo si el usuario lo solicita)

Baloto juega **lunes, miércoles y sábado** alrededor de las 11:00 p. m. (hora de Colombia). Si el usuario pide automatizar la revisión sin tener el navegador abierto, puede programar `--check` (por ejemplo, martes/jueves/domingo a las 8:00 a. m.):

```cron
0 8 * * 0,2,4 /usr/bin/env python3 /ruta/a/skills/baloto/scripts/baloto.py --check >> ~/.local/share/baloto/check.log 2>&1
```
