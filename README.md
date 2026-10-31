# Baloto — Agent Skill estándar (Historial, Carrito y Seguimiento en COP)

Skill portable para **Google Antigravity**, **Claude Code** y **Codex** que cubre el flujo real de un jugador de Baloto en Colombia usando únicamente **Python 3.10+ estándar** (cero dependencias externas obligatorias):

1. **Historial oficial incluido (`skills/baloto/data/historial_baloto.csv`):** Registro verificado de sorteos de **Baloto y Revancha** (desde `#2360` en enero de 2024 hasta el presente, actualizable con un comando desde `baloto.com`), listo para explorar frecuencias, rezagos, $\chi^2$, pares o entrenar tus propios modelos estadísticos para escoger números.
2. **Preparación de jugadas y carrito en `baloto.com`:** Valida combinaciones (`Solo Baloto` a $6.000 COP o `Baloto + Revancha` a $9.000 COP, tarifa editable), las organiza en boletas oficiales de hasta 5 paneles (`A`–`E`) y guía al navegador del agente (con apoyo de `carrito_helper.py`), dejando siempre el inicio de sesión y el pago en manos del usuario.
3. **Seguimiento automático de premios reales en COP:** Tablero web local y CLI que consultan `baloto.com/resultados` y las tablas oficiales de premio por ganador por categoría para calcular aciertos línea por línea y el balance real de tus tiquetes.

> Proyecto independiente, sin afiliación con Operador Nacional de Juegos ni Coljuegos. Nunca almacena credenciales ni ejecuta compras o pagos por su cuenta.

---

## Instalación en tus agentes

Requiere **Python 3.10+**:

```sh
git clone https://github.com/Juan8721/baloto-skill.git
cd baloto-skill
python3 scripts/install.py --agent all
```

También puedes instalar en un agente específico (`--agent antigravity`, `--agent claude` o `--agent codex`) o actualizar una copia previa pasando `--force`:

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
- En **Mis tiquetes** puedes registrar combinaciones en carrito (`En preparación`) o marcar tiquetes ya comprados para verificar resultados oficiales en COP.
- En **Historial y estadística** (`/estadisticas`) puedes explorar frecuencias, puntuaciones *z*, prueba $\chi^2$ de uniformidad y rezagos tanto para **Baloto** como para **Revancha**.

### 2. Sincronizar el historial y consultar estadísticas por terminal

```sh
# Traer los sorteos más recientes publicados en baloto.com hacia historial_baloto.csv
python3 skills/baloto/scripts/baloto.py --sync-history

# Consultar frecuencias, rezagos y χ² (baloto, revancha o both)
python3 skills/baloto/scripts/baloto.py --stats --game baloto
python3 skills/baloto/scripts/baloto.py --stats --game both --last 60 --json
```

### 3. Preparar boletas para el carrito de `baloto.com`

Crea un JSON **fuera del repositorio** (por ejemplo `/tmp/jugada.json`):

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
# Registrar jugada validada
python3 skills/baloto/scripts/baloto.py --prepare /tmp/jugada.json --no-serve

# Generar plan de boletas (paneles A–E) o asistente DOM auditable para el navegador del agente
python3 skills/baloto/scripts/carrito_helper.py --input /tmp/jugada.json --mode plan
python3 skills/baloto/scripts/carrito_helper.py --input /tmp/jugada.json --mode js
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
| [`skills/baloto/scripts/baloto.py`](skills/baloto/scripts/baloto.py) | Motor de validación, sincronizador de `baloto.com`, cálculo de premios oficiales en COP, estadística y servidor HTTP |
| [`skills/baloto/scripts/carrito_helper.py`](skills/baloto/scripts/carrito_helper.py) | Agrupador en boletas de 5 paneles (`A`–`E`) y asistente auditable para el carrito web (se detiene antes del pago) |
| [`skills/baloto/scripts/web.html`](skills/baloto/scripts/web.html) | Tablero local con seguimiento en COP y explorador estadístico del historial |
| [`skills/baloto/references/`](skills/baloto/references/) | Guías detalladas de [modelos estadísticos](skills/baloto/references/modelo-estadistico.md), [carrito en navegador](skills/baloto/references/carrito-navegador.md) y [seguimiento por CLI](skills/baloto/references/seguimiento-cli.md) |
| [`scripts/install.py`](scripts/install.py) | Instalador multi-agente (`antigravity`, `claude`, `codex`, `all`) |
| [`tests/test_baloto.py`](tests/test_baloto.py) | Suite automatizada de pruebas |

---

## Privacidad y pruebas

- Los tiquetes personales se guardan fuera del repositorio en `~/.local/share/baloto/state.json` con escritura atómica y permisos restringidos (`0700`).
- El servidor escucha únicamente en `127.0.0.1` y protege mutaciones con validación de `Host`, `Origin`, cabeceras CSP con `nonce` y token CSRF.

Ejecutar toda la suite de pruebas (sin red ni cuentas externas):

```sh
python3 -m unittest discover -s tests -v
```

Licencia [MIT](LICENSE).
