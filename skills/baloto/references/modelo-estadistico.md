# Historial Oficial y Modelos Estadísticos

El paquete incluye el historial verificado de sorteos de **Baloto y Revancha** en [`../data/historial_baloto.csv`](../data/historial_baloto.csv) para que cualquier persona o agente pueda construir análisis exploratorios, filtros combinatorios o ruedas sistemáticas sin buscar datos dispersos.

## 1. Esquema del dataset (`data/historial_baloto.csv`)

Archivo CSV UTF-8 (finales de línea `LF`) ordenado ascendentemente por número de sorteo sin huecos ni duplicados:

| Columna | Tipo | Descripción |
|---|---|---|
| `sorteo` | `int` | Número oficial del sorteo (p. ej. `2360` … `2728+`) |
| `fecha` | `YYYY-MM-DD` | Fecha oficial en formato ISO para ordenar y filtrar directamente |
| `b1..b5` | `int` (`1..43`) | Cinco balotas principales de Baloto ordenadas de menor a mayor |
| `sb_baloto` | `int` (`1..16`) | Superbalota oficial de Baloto |
| `r1..r5` | `int` (`1..43`) | Cinco balotas principales de Revancha ordenadas de menor a mayor |
| `sb_revancha` | `int` (`1..16`) | Superbalota oficial de Revancha |

### Mantener el historial actualizado

Antes de correr un modelo nuevo, puedes traer automáticamente los sorteos más recientes publicados en `baloto.com`:

```sh
python3 scripts/baloto.py --sync-history
```

Si deseas refrescar o auditar un tramo específico desde `baloto.com`:

```sh
python3 scripts/baloto.py --sync-history --from-draw 2700
```

## 2. Estadísticas integradas por CLI y JSON

```sh
# Resumen en texto (todo el historial de Baloto)
python3 scripts/baloto.py --stats --game baloto

# Últimos 60 sorteos combinando Baloto + Revancha en JSON para el agente
python3 scripts/baloto.py --stats --game both --last 60 --json
```

Métricas calculadas por `compute_stats` en [`../scripts/baloto.py`](../scripts/baloto.py):
- **Frecuencia observada vs. esperada** ($E = M \times 5/43$ en balotas y $E = M / 16$ en Superbalota) y **puntuación $z$ hipergeométrica/binomial** para cada número.
- **Prueba $\chi^2$ de bondad de ajuste corregida sin reemplazo:** al extraer $k=5$ balotas distintas de $N=43$ en cada sorteo, $\mathbb{E}[\sum_{i=1}^{43} (C_i - E)^2 / E] = N - k = 38$, por lo que se aplica el factor exacto $\frac{N-1}{N-k} = \frac{42}{38}$ para comparar contra $\chi^2_{42}$ (umbral $p=0{,}05$: `58,12`), junto con $\chi^2_{15}$ en Superbalota (umbral $p=0{,}05$: `25,00`).
- **Rezago (`draws_since_seen`):** cuántos sorteos consecutivos lleva cada balota o Superbalota sin salir (`0` = salió en el último sorteo).
- **Pares más frecuentes (`top_pairs`)** y **estadísticos de la suma** (`min`, `max`, `mean`, `median`) de las 5 balotas.

## 3. Guía para construir modelos propios en Python

El agente puede leer [`../data/historial_baloto.csv`](../data/historial_baloto.csv) directamente con el módulo estándar `csv` (o `pandas` si el entorno del usuario lo tiene) para implementar el enfoque que pida el usuario:

1. **Muestreo ponderado por frecuencia o rezago:** asignar pesos suaves a números según ventana reciente o sorteos sin aparecer.
2. **Filtros estructurales de suma y paridad:** restringir combinaciones cuya suma de 5 balotas caiga cerca del rango intercuartílico histórico (~90–130) o equilibrar pares/impares (2–3 o 3–2) y decenas.
3. **Diseño sistemático / cobertura (*wheeling*):** dado un subconjunto de $k$ números elegidos por el usuario (p. ej. 10–15 balotas), generar $N$ líneas que maximicen la cobertura de ternas `(3 aciertos)` sin duplicar combinaciones.
4. **Optimización de pago esperado en caso de acierto (anti-colisión):** evitar fechas de cumpleaños (`1..31` concentrados), secuencias aritméticas (`01-02-03-04-05`) o patrones geométricos del tarjetón reduce la probabilidad de compartir un acumulado con otros apostadores.
