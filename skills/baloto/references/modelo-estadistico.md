# Historial Oficial y Modelos Estadísticos

El paquete incluye el historial verificado de sorteos de **Baloto y Revancha** en [`../data/historial_baloto.csv`](../data/historial_baloto.csv) para que cualquier persona o agente pueda construir análisis exploratorios, filtros combinatorios o ruedas sistemáticas sin buscar datos dispersos.

## 1. Esquema del dataset (`data/historial_baloto.csv`)

Archivo CSV UTF-8 (finales de línea `LF`) ordenado ascendentemente por número de sorteo, sin duplicados, desde el `#2104` (21-jul-2021). Cada sorteo fue contrastado con su página oficial de detalle en `baloto.com` y cruzado con el dataset independiente [Cha0smagick/Baloto-oracle](https://github.com/Cha0smagick/Baloto-oracle) (MIT). El único número ausente es el `#2685`: `baloto.com` no lo publica (el 20-jul-2026, festivo, no hubo sorteo y la numeración salta del `#2684` al `#2686`).

| Columna | Tipo | Descripción |
|---|---|---|
| `sorteo` | `int` | Número oficial del sorteo (p. ej. `2104` … `2729+`) |
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
- **Pares más frecuentes (`top_pairs`)** y **estadísticos de la suma** (`min`, `max`, `mean`, `median`, `std`, y cuartiles `q25`–`q75`).
- **Balance Par / Impar (`odd_even`):** conteo y porcentajes de patrones (`3I-2P`, `2I-3P`, `4I-1P`, etc.). En más del 65% de los sorteos observados la combinación tiene 2 o 3 impares.
- **Balance Alto / Bajo (`high_low`):** distribución de números entre la mitad baja (`1–22`) y la mitad alta (`23–43`).
- **Números consecutivos (`consecutive_pairs`):** porcentaje de sorteos con 0 pares consecutivos vs. al menos 1 par consecutivo (~41% histórico).
- **Modelo Bayesiano Beta-Binomial (`bayesian`):** probabilidades posteriores normalizadas combinando el prior histórico y la verosimilitud de los últimos 50 sorteos.

## 3. Generación de jugadas sugeridas por CLI y Web

El motor incluye un generador estocástico con filtros combinatorios y teoría de juegos:

```sh
# Sugerencia bayesiana (Beta-Binomial)
python3 scripts/baloto.py --suggest 3 --strategy bayesian

# Sugerencia con filtros estructurales (Par/Impar 3/2 o 2/3, IQR de suma, ≤1 consecutivo)
python3 scripts/baloto.py --suggest 2 --strategy balanced

# Estrategia anti-colisión (al menos 2 números >31, sin patrones de calendario)
python3 scripts/baloto.py --suggest 1 --strategy anti_collision

# Piping directo para registrar en el carrito local
python3 scripts/baloto.py --suggest 2 --strategy balanced | python3 scripts/baloto.py --prepare - --no-serve
```

Estrategias disponibles:
1. `bayesian`: Muestreo ponderado por actualización conjugada Beta-Binomial ($\alpha = C_{\text{hist}} + 1 + C_{\text{rec}}, \beta = T_{\text{hist}} - C_{\text{hist}} + 1 + T_{\text{rec}} - C_{\text{rec}}$) con descarte de combinaciones íntegramente de cumpleaños ($\le 31$).
2. `balanced`: Fuerza paridad equilibrada (3/2 o 2/3), balance alto/bajo, suma en rango intercuartílico ($Q_1 \le \sum \le Q_3$) y máximo un par consecutivo.
3. `anti_collision`: Diseñada desde la teoría de juegos para maximizar el retorno esperado en caso de acierto; favorece números $\ge 32$ y penaliza números sobrejugados por fechas de nacimiento o patrones geométricos para no dividir acumulados.
4. `hot`: Favorece números con mayor frecuencia relativa reciente ($z > 0$).
5. `cold`: Favorece números con mayor rezago de sorteos consecutivos sin salir.

## 4. Rigor científico: Validación empírica de no-predictibilidad

El dataset fue analizado exhaustivamente por el profesor Alejandro Quintero en [Cha0smagick/Baloto-oracle](https://github.com/Cha0smagick/Baloto-oracle) mediante pruebas *walk-forward out-of-sample* (entrenar modelos Random Forest, Regresión Logística, Cadenas de Markov y Modelos Bayesianos exclusivamente con el pasado y evaluar en sorteos futuros reales sin filtración de datos).

Conclusiones empíricas clave:
- **Ausencia de poder predictivo:** Ningún modelo estadístico ni de aprendizaje automático logra un *lift* estadísticamente significativo sobre la línea base del azar ($5/43 \approx 11{,}63\%$).
- **Aleatoriedad confirmada:** Las pruebas de rachas de Wald-Wolfowitz, Ljung-Box y bondad de ajuste $\chi^2$ no rechazan la hipótesis nula $H_0$ de independencia estocástica entre sorteos.
- **Propósito real de las estrategias:** Los filtros combinatorios y el modelo bayesiano son herramientas para estructuración sistemática y **optimización de valor esperado anti-colisión** (no compartir premio), nunca métodos para garantizar aciertos sobre el bombo.
