# Baloto — Agent Skill estándar (Historial, Carrito y Seguimiento en COP)

Skill portable para **Google Antigravity**, **Claude Code** y **Codex** que cubre el flujo real de un jugador de Baloto en Colombia usando únicamente **Python 3.10+ estándar** (cero dependencias externas obligatorias):

1. **Historial oficial incluido ([`skills/baloto/data/historial_baloto.csv`](skills/baloto/data/historial_baloto.csv)):** Registro íntegro y verificado de sorteos de **Baloto y Revancha** (desde `#2104` el 21 de julio de 2021 hasta el presente, actualizable con un comando desde `baloto.com`), listo para explorar frecuencias, rezagos, $\chi^2$ corregido sin reemplazo, pares o entrenar tus propios modelos estadísticos para escoger números.
2. **Preparación de jugadas y carrito oficial (`apuestaaqui.baloto.com`):** Valida combinaciones (`Solo Baloto` a $6.000 COP o `Baloto + Revancha` a $9.000 COP, tarifa editable), las consolida en boletas oficiales de hasta 5 paneles (`A`–`E`) y guía al navegador del agente con [`carrito_helper.py`](skills/baloto/scripts/carrito_helper.py), dejando siempre el inicio de sesión y el pago en manos del usuario.
3. **Seguimiento automático de premios reales en COP:** Tablero web local accesible (WCAG 2.1 AA) y CLI que consultan `baloto.com/resultados` y las tablas oficiales de premio por ganador por categoría (`5+SB` hasta `0 o 1 + SB`) para calcular aciertos línea por línea y el balance real de tus tiquetes una vez liquidado el escrutinio oficial.

> Proyecto independiente, sin afiliación con Operador Nacional de Juegos ni Coljuegos. Nunca almacena credenciales ni ejecuta compras o pagos por su cuenta.

---

## Instalación en tus agentes

Requiere **Python 3.10+** *(en Windows usa `python` o `py -3` si `python3` no está en `PATH`)*:

```sh
git clone https://github.com/Juan8721/baloto-skill.git
cd baloto-skill
python3 scripts/install.py --agent all
```

También puedes instalar en un agente específico (`--agent antigravity`, `--agent claude` o `--agent codex`) o actualizar una copia previa pasando `--force` (conservando automáticamente cualquier sorteo nuevo sincronizado en tu historial local):

| Agente | Ruta de instalación | Cómo invocarla |
|---|---|---|
| **Antigravity** | `~/.gemini/config/skills/baloto/` | Pide en el chat: *«Usa la skill baloto para analizar el historial y armar mi jugada»* |
| **Claude Code** | `~/.claude/skills/baloto/` | `/baloto` |
| **Codex** | `~/.agents/skills/baloto/` | `Usa $baloto para verificar mis tiquetes o preparar mi carrito.` |

---

## Inicio rápido (Tablero Web y CLI)

### 1. Abrir el tablero local de seguimiento y estadística

```sh
python3 skills/baloto/scripts/baloto.py --port 8765
```

Abre **http://127.0.0.1:8765** en tu navegador:
- En **Mis tiquetes** puedes registrar combinaciones en carrito con validación en vivo o marcar tiquetes ya comprados para verificar resultados oficiales en COP.
- En **Historial y estadística** (`/estadisticas`) puedes explorar frecuencias, puntuaciones *z*, prueba $\chi^2$ corregida sin reemplazo, pares frecuentes y rezagos tanto para **Baloto** como para **Revancha**.

### 2. Sincronizar el historial y consultar estadísticas por terminal

```sh
# Traer los sorteos más recientes publicados en baloto.com hacia historial_baloto.csv
python3 skills/baloto/scripts/baloto.py --sync-history

# Consultar frecuencias, rezagos y χ² (baloto, revancha o both)
python3 skills/baloto/scripts/baloto.py --stats --game baloto
python3 skills/baloto/scripts/baloto.py --stats --game both --last 60 --json

# Generar combinaciones sugeridas con modelos combinatorios (bayesian, balanced, anti_collision, hot, cold)
python3 skills/baloto/scripts/baloto.py --suggest 3 --strategy bayesian
python3 skills/baloto/scripts/baloto.py --suggest 2 --strategy balanced
```

### 3. Preparar boletas para el carrito oficial y confirmar tras la compra

Puedes pasar la jugada por `stdin` (`--prepare -`) o desde un JSON externo fuera del repositorio (p. ej. `/tmp/jugada.json`):

```json
{
  "label": "Jugada sábado",
  "mode": "baloto_revancha",
  "start_draw": 2729,
  "draw_count": 3,
  "already_purchased": false,
  "lines": "Combo A: 04 15 23 34 41 | 12\nCombo A: 07 11 19 28 42 | 08"
}
```

```sh
# Registrar jugada validada en carrito
python3 skills/baloto/scripts/baloto.py --prepare /tmp/jugada.json --no-serve

# Generar plan de boletas (paneles A–E) o asistente DOM seguro (sin innerHTML) para el navegador del agente
python3 skills/baloto/scripts/carrito_helper.py --ticket-id <ticket_id> --mode plan
python3 skills/baloto/scripts/carrito_helper.py --ticket-id <ticket_id> --mode js

# Confirmar el tiquete una vez pagado en apuestaaqui.baloto.com
python3 skills/baloto/scripts/baloto.py --confirm <ticket_id> --start-draw 2729
```

### 4. Verificar tiquetes activos (manual o vía `cron`)

```sh
python3 skills/baloto/scripts/baloto.py --check
```

---

## Estructura del paquete

| Archivo | Propósito |
|---|---|
| [`skills/baloto/SKILL.md`](skills/baloto/SKILL.md) | Instrucciones principales y límites operativos para el agente |
| [`skills/baloto/data/historial_baloto.csv`](skills/baloto/data/historial_baloto.csv) | Historial oficial de resultados de Baloto y Revancha (`YYYY-MM-DD`, balotas y Superbalotas) |
| [`skills/baloto/scripts/baloto.py`](skills/baloto/scripts/baloto.py) | Motor de validación, scrapers endurecidos de `baloto.com`, cálculo de premios oficiales en COP, estadística y servidor HTTP |
| [`skills/baloto/scripts/carrito_helper.py`](skills/baloto/scripts/carrito_helper.py) | Agrupador en boletas de 5 paneles (`A`–`E`) y asistente DOM seguro para el carrito web (se detiene antes del pago) |
| [`skills/baloto/scripts/web.html`](skills/baloto/scripts/web.html) | Tablero local accesible (WCAG 2.1 AA) con seguimiento separado de Baloto/Revancha y explorador estadístico |
| [`skills/baloto/references/`](skills/baloto/references/) | Guías detalladas de [modelos estadísticos](skills/baloto/references/modelo-estadistico.md), [carrito en navegador](skills/baloto/references/carrito-navegador.md) y [seguimiento por CLI](skills/baloto/references/seguimiento-cli.md) |
| [`scripts/install.py`](scripts/install.py) | Instalador atómico multi-agente (`antigravity`, `claude`, `codex`, `all`) |
| [`tests/test_baloto.py`](tests/test_baloto.py) | Suite automatizada de pruebas de regresión, seguridad y mutación |

---

## Privacidad y pruebas

- Los tiquetes personales se guardan fuera del repositorio en `~/.local/share/baloto/state.json` (o `%LOCALAPPDATA%\baloto\state.json` en Windows) con escritura atómica, bloqueo transaccional (`.store.lock`) y permisos restringidos (`0700` directorio / `0600` archivo).
- El servidor escucha únicamente en `127.0.0.1` y protege las operaciones con validación de `Host`, `Origin`, `Sec-Fetch-Site`, CSP con `nonce` aleatorio por respuesta y token CSRF independiente.

Ejecutar toda la suite de pruebas (sin red ni cuentas externas):

```sh
python3 -m unittest discover -s tests -v
```

Licencia [MIT](LICENSE).
