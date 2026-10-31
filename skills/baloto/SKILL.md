---
name: baloto
description: >-
  Gestiona jugadas reales de Baloto y Revancha en Colombia: explora y modela sobre el historial oficial de sorteos incluido, prepara y carga combinaciones en el carrito de baloto.com (dejando login y pago al usuario), y verifica resultados y premios oficiales en COP. Usar cuando el usuario quiera elegir números con datos históricos, armar su carrito de Baloto o llevar seguimiento de sus tiquetes.
---

# Baloto — Historial Estadístico, Carrito y Seguimiento Oficial

Esta skill acompaña todo el ciclo real de una jugada de Baloto en Colombia con **Python 3.10+ estándar** (sin dependencias externas obligatorias):

1. **Análisis estadístico e historial oficial** (`data/historial_baloto.csv`): consultar frecuencias, rezagos, $\chi^2$, pares o ejecutar modelos propios del usuario sobre los sorteos reales de Baloto y Revancha.
2. **Preparación de jugada y carrito en `baloto.com`**: validar combinaciones, agruparlas en boletas de hasta 5 paneles (`A`–`E`), calcular el valor exacto en pesos colombianos (COP) y cargarlas en el navegador del usuario deteniéndose siempre antes del pago.
3. **Seguimiento oficial de tiquetes**: consultar `baloto.com/resultados` y las tablas oficiales de premio por ganador por categoría para calcular aciertos y ganancias reales en COP.

## 1. Conversación inicial y reglas del juego

Reutiliza los datos que la persona ya haya entregado y pregunta únicamente lo que falte:

- **Origen de las líneas:** ¿Ya tiene sus combinaciones o quiere explorar `data/historial_baloto.csv` / aplicar un criterio estadístico para construirlas?
  - Formato por línea: `01 02 03 04 05 | 06` (5 números distintos del `1` al `43` + Superbalota del `1` al `16`), con etiqueta opcional de grupo como `Combo A: 01 02 03 04 05 | 06`.
- **Modalidad y vigencia:**
  - `baloto_revancha` (**$9.000 COP** por línea y sorteo por defecto) o `baloto` Solo Baloto (**$6.000 COP** por línea y sorteo por defecto). El valor unitario es ajustable si cambia la tarifa oficial.
  - Sorteo inicial (`#XXXX`, por defecto el próximo sorteo tras el último del historial) y cantidad de sorteos consecutivos (`1..52`; Baloto juega lunes, miércoles y sábado).
- **Estado del tiquete:** ¿Quiere **preparar el carrito** para comprarlo en `baloto.com`, o **ya compró el tiquete** y desea activar la verificación inmediata?

> **Transparencia estadística:** Todos los sorteos son eventos independientes con probabilidad uniforme ($\binom{43}{5} \times 16 = 15.401.568$ combinaciones). Cuando el usuario pida modelos estadísticos (frecuencias, rezagos, cobertura sistemática o evitar números populares para no dividir premios), ejecútalos con rigor sobre `data/historial_baloto.csv` sin prometer ventaja predictiva sobre el bombo. Ver [references/modelo-estadistico.md](./references/modelo-estadistico.md).

## 2. Comandos principales (usar la ruta real de esta skill)

Resuelve siempre la carpeta donde está instalada esta skill (no asumas rutas fijas de un usuario):

- **Sincronizar el historial oficial con `baloto.com`:**
  ```sh
  python3 scripts/baloto.py --sync-history
  ```
- **Consultar estadísticas rápidas del historial (`baloto`, `revancha` o `both`):**
  ```sh
  python3 scripts/baloto.py --stats --game baloto
  ```
- **Levantar el tablero web local (`http://127.0.0.1:8765`):**
  ```sh
  python3 scripts/baloto.py --port 8765
  ```
  *(Si el puerto está ocupado, elige otro como `--port 8766`, verifica `/api/health` y entrega la URL efectiva).*
- **Verificar desde CLI o tarea programada todos los tiquetes activos:**
  ```sh
  python3 scripts/baloto.py --check
  ```

Consulta [references/seguimiento-cli.md](./references/seguimiento-cli.md) para registrar jugadas por JSON (`--prepare`), persistencia privada en `~/.local/share/baloto` y configuración opcional en `cron` / `launchd`.

## 3. Cargar las combinaciones en el carrito de `baloto.com`

Cuando el usuario pida preparar su compra en `baloto.com`:

1. Registra y valida primero la jugada en estado `cart` (vía tablero o `scripts/baloto.py --prepare jugada.json --no-serve`) para descartar duplicados o números fuera de rango y confirmar el total en COP.
2. Genera el plan determinista de boletas de 5 paneles (`A`–`E`):
   ```sh
   python3 scripts/carrito_helper.py --ticket-id <id> --mode plan
   ```
3. **Guía el navegador del agente** sobre la sesión abierta del usuario en `baloto.com`. Si son muchas líneas o necesitas una guía visual/DOM en la página, apóyate en el snippet auditable:
   ```sh
   python3 scripts/carrito_helper.py --ticket-id <id> --mode js
   ```
   Lee el procedimiento completo en [references/carrito-navegador.md](./references/carrito-navegador.md).

## 4. Límites estrictos de seguridad y privacidad

- **Login y pago exclusivos del usuario:** Nunca solicites ni almacenes contraseñas, cédulas, tokens de sesión ni datos de tarjeta/PSE. No automatices el inicio de sesión, no evadas CAPTCHAs ni controles de acceso, y **detente siempre en el resumen del carrito** antes de cualquier botón de pago.
- **Premios oficiales verificables:** Nunca inventes ni estimes montos de premios; `scripts/baloto.py` lee únicamente la tabla oficial de 8 categorías publicada en `baloto.com/resultados-baloto/<sorteo>` y `baloto.com/resultados-revancha/<sorteo>`.
- **Privacidad de jugadas:** Guarda los tiquetes personales fuera del repositorio (por defecto `~/.local/share/baloto`). Solo el historial público de resultados (`data/historial_baloto.csv`) se versiona en Git.
