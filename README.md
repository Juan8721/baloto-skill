# Laboratorio de sorteos — skill educativa

Una demostración de cómo construir una **Agent Skill compatible con Codex y Claude Code**, desde las preguntas iniciales hasta un script determinista con interfaz web y pruebas.

**Todo es simulado:** créditos sin valor monetario, sesión local de demostración, carrito ficticio y resultados sintéticos. Este proyecto independiente no está afiliado con Baloto, no accede a su web, no autentica usuarios del operador y no compra apuestas ni procesa pagos.

## Qué puedes probar

- Introducir tus propias combinaciones de ejemplo, incluso cientos de líneas.
- Configurar un límite de créditos, cantidad de sorteos e intervalo entre ellos.
- Validar números, duplicados y costo antes de preparar el carrito.
- Dejar el carrito en borrador o confirmar la simulación.
- Abrir una URL local de seguimiento para cada experimento.
- Comprobar resultados sintéticos un día después de cada sorteo simulado.
- Avanzar el reloj para probarlo en minutos y conservar el estado entre reinicios.

La tabla de créditos es inventada. No sirve para estimar premios ni diseñar estrategias de apuestas reales.

## Inicio rápido

Requiere **Python 3.10+**, sin dependencias externas. El código utiliza la biblioteca estándar.

```sh
git clone https://github.com/Juan8721/baloto-simulador-skill.git
cd baloto-simulador-skill
python3 skills/baloto-simulador/scripts/demo.py
```

Abre **http://127.0.0.1:8765**. En Windows, puedes sustituir `python3` por `py -3`.

1. Revisa los números sintéticos precargados o introduce otros ejemplos.
2. Define los créditos y los sorteos; pulsa **Preparar carrito demo**.
3. Guarda la URL `/seguimiento/<id>` y pulsa **Confirmar simulación**.
4. Pulsa **Avanzar un día** dos veces para ver la primera comprobación.

El servidor comprueba los experimentos cada 30 segundos y actualiza el tablero. La primera fecha de sorteo es un día después de crear el carrito; sus resultados están disponibles un día después de esa fecha. Todas las fechas del tablero indican UTC.

El seguimiento automático funciona mientras el script está activo. Detén con `Ctrl+C`; al reiniciar con la misma carpeta, conserva el estado y procesa los sorteos vencidos. No instala un servicio de arranque ni ejecuta procesos en la nube.

## Instalar la skill

```sh
python3 scripts/install.py --agent both
```

También acepta `--agent codex` o `--agent claude`. Copia el paquete en las carpetas personales estándar y rechaza sobrescribir instalaciones existentes:

| Agente | Destino | Ejemplo de invocación |
|---|---|---|
| Codex | `~/.agents/skills/baloto-simulador/` | `Usa $baloto-simulador para crear una prueba con mis números.` |
| Claude Code | `~/.claude/skills/baloto-simulador/` | `/baloto-simulador` |

Si el agente no la muestra, inicia una sesión nueva. El formato común usa `SKILL.md` con `name` y `description`; `agents/openai.yaml` añade metadatos opcionales para Codex. La instalación por sí sola no inicia el servidor.

Referencias del formato: [skills en Codex](https://learn.chatgpt.com/docs/build-skills) y [skills en Claude Code](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview#claude-code). El paquete usa herramientas locales estándar y no depende de un MCP ni del nombre de un usuario.

## Automatizar la preparación

Crea un JSON **fuera del repositorio**, por ejemplo:

```json
{
  "label": "Prueba educativa",
  "budget": 20,
  "draw_count": 3,
  "interval_days": 1,
  "lines": "01 02 03 04 05 | 06\n07 08 09 10 11 | 12"
}
```

```sh
python3 skills/baloto-simulador/scripts/demo.py --prepare /ruta/prueba.json
```

Prepara el carrito, arranca la interfaz e imprime la URL de seguimiento. La confirmación ficticia queda en la interfaz. Cada ejecución con `--prepare` crea un experimento nuevo; para recuperar los anteriores, arranca sin esa opción.

Opciones: `--port 8766`, `--data-dir /ruta/privada`, `--poll-seconds 30`. Consulta [el uso detallado](skills/baloto-simulador/references/uso.md).

## Cómo está construida

| Archivo | Qué enseña |
|---|---|
| [SKILL.md](skills/baloto-simulador/SKILL.md) | Descubrimiento, preguntas, decisiones y límites del flujo |
| [demo.py](skills/baloto-simulador/scripts/demo.py) | Validación, persistencia, reloj y cálculo repetible |
| [web.html](skills/baloto-simulador/scripts/web.html) | Formulario, carrito y seguimiento local |
| [uso.md](skills/baloto-simulador/references/uso.md) | Detalles que el agente carga solo cuando hacen falta |
| [openai.yaml](skills/baloto-simulador/agents/openai.yaml) | Nombre e invocación sugerida en Codex |
| [tests](tests/test_demo.py) | Casos observables: fechas, duplicados, presupuesto, reinicio y errores |

La habilidad de conversar vive en las instrucciones; las cuentas y los cambios de estado viven en el script. No es necesario llamar a un modelo para cada actualización periódica.

## Datos y alcance

Los datos se guardan por defecto en `~/.local/share/baloto-simulador`, fuera del repositorio. No subas tus entradas ni el estado a GitHub. El servidor escucha únicamente en `127.0.0.1`; su URL funciona en el equipo que lo ejecuta. No incluye autenticación multiusuario y no debe exponerse directamente a internet.

El motor y el instalador son Python portátil. El proceso local se prueba con la suite incluida; la detección de una skill depende de la versión y configuración de cada agente. No se incluye un adaptador para operaciones reales.

## Pruebas

```sh
python3 -m unittest discover -s tests -v
```

Usan datos sintéticos, carpetas temporales y ninguna cuenta externa. La integración continua ejecuta estas pruebas en macOS, Linux y Windows.

Licencia [MIT](LICENSE).
