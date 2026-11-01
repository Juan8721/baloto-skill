#!/usr/bin/env python3
"""Herramienta estándar de Baloto: historial oficial, estadística, preparación y seguimiento real en COP."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import copy
import csv
from datetime import datetime, timezone
import html as html_lib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import combinations
import json
import math
import os
from pathlib import Path
import random
import re
import secrets
import signal
import stat
import statistics
import sys
import tempfile
import threading
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
import urllib.request
import uuid

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]

UTC = timezone.utc
ALLOWED_BALOTO_HOSTS = frozenset({'baloto.com', 'www.baloto.com'})
RESULTADOS_URL = 'https://baloto.com/resultados'
DETALLE_BALOTO_URL = 'https://baloto.com/resultados-baloto/{sorteo}'
DETALLE_REVANCHA_URL = 'https://baloto.com/resultados-revancha/{sorteo}'
USER_AGENT = 'Mozilla/5.0 (compatible; baloto-skill/1.1)'
MAX_HTML_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_AUTO_SYNC_SPAN = 250
PANEL_LETTERS = ('A', 'B', 'C', 'D', 'E')

DEFAULT_PRICES = {
    'baloto': 6000,
    'baloto_revancha': 9000,
}

# Orden oficial de las 8 categorías publicadas por baloto.com en cada sorteo.
# '0 o 1 + SB' agrupa tanto 0 aciertos + Superbalota como 1 acierto + Superbalota.
PRIZE_CATEGORY_KEYS = [
    '5+SB', '5', '4+SB', '4', '3+SB', '3', '2+SB', '0 o 1 + SB',
]

MESES = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6,
    'julio': 7, 'agosto': 8, 'septiembre': 9, 'octubre': 10, 'noviembre': 11,
    'diciembre': 12,
}

HISTORY_COLUMNS = [
    'sorteo', 'fecha',
    'b1', 'b2', 'b3', 'b4', 'b5', 'sb_baloto',
    'r1', 'r2', 'r3', 'r4', 'r5', 'sb_revancha',
]

SUGGESTION_STRATEGIES = {
    'bayesian': 'Ponderación bayesiana Beta-Binomial (prior histórico + verosimilitud reciente de 50 sorteos) con filtro anti-cumpleaños.',
    'balanced': 'Filtros estructurales históricos: balance Par/Impar (3/2 o 2/3), Alto/Bajo (1-22 vs 23-43) y suma en rango intercuartílico.',
    'hot': 'Favorece los números con mayor frecuencia relativa y z-score positivo en la ventana analizada.',
    'cold': 'Favorece los números con mayor rezago de sorteos consecutivos sin salir.',
    'anti_collision': 'Teoría de juegos anti-colisión: al menos 2 números entre 32 y 43 y sin patrones de calendario para no dividir acumulados.',
}

DEFAULT_HISTORY_PATH = Path(__file__).resolve().parents[1] / 'data/historial_baloto.csv'
COMBO_LABEL_RE = re.compile(r'[\w\s.#-]+', re.UNICODE)

_HISTORY_CACHE: dict[Path, tuple[int, int, list[dict]]] = {}
_HISTORY_CACHE_LOCK = threading.Lock()


class UpstreamSyncError(ValueError):
    """Error al consultar o interpretar una respuesta HTML oficial de baloto.com."""


class DrawNotFoundError(UpstreamSyncError):
    """baloto.com no publica ese número de sorteo (p. ej. #2685, omitido por el festivo del 20-jul-2026)."""


def reconfigure_stdio() -> None:
    """Fuerza UTF-8 en stdout/stderr para evitar fallos con cp1252 en Windows."""
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, 'reconfigure', None)
        if callable(reconf):
            try:
                reconf(encoding='utf-8', errors='replace')
            except (OSError, ValueError):
                pass


def default_data_dir() -> Path:
    env_dir = os.environ.get('BALOTO_DATA_DIR', '').strip()
    if env_dir:
        return Path(env_dir).expanduser()
    legacy = Path.home() / '.local/share/baloto'
    if legacy.exists():
        return legacy
    if os.name == 'nt':
        local_app = os.environ.get('LOCALAPPDATA', '').strip()
        if local_app:
            return Path(local_app) / 'baloto'
    xdg_data = os.environ.get('XDG_DATA_HOME', '').strip()
    if xdg_data:
        return Path(xdg_data).expanduser() / 'baloto'
    return legacy


def now() -> datetime:
    return datetime.now(UTC)


def integer(value, field: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{field}: debe ser un entero entre {low} y {high}.')
    return value


def fmt_cop(amount: int) -> str:
    sign = '−' if amount < 0 else ''
    return f"{sign}${abs(amount):,}".replace(',', '.')


def _ensure_private_dir(directory: Path) -> Path:
    raw = Path(directory).expanduser()
    if raw.is_symlink():
        raise ValueError(f'El directorio de datos no puede ser un enlace simbólico: {raw}')
    resolved = raw.resolve()
    resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != 'nt':
        try:
            st = resolved.stat()
            if st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) != 0o700:
                os.chmod(resolved, 0o700)
        except OSError:
            pass
    return resolved


def atomic_write_text(path: Path, content: str, mode: int | None = None) -> None:
    raw_path = Path(path).expanduser()
    if raw_path.is_symlink():
        raise ValueError(f'No se permite sobrescribir un enlace simbólico: {raw_path}')
    parent = raw_path.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / raw_path.name
    if target.is_symlink():
        raise ValueError(f'No se permite sobrescribir un enlace simbólico: {target}')

    if mode is not None:
        target_mode = mode
    elif target.exists():
        target_mode = stat.S_IMODE(target.stat().st_mode)
    else:
        target_mode = 0o644

    fd, temp = tempfile.mkstemp(prefix=f'.{target.name}-', dir=parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != 'nt':
            os.chmod(temp, target_mode)
        os.replace(temp, target)
        if os.name != 'nt':
            try:
                dir_fd = os.open(parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def file_lock(lock_path: Path, non_blocking: bool = False):
    raw = Path(lock_path).expanduser()
    if raw.is_symlink():
        raise ValueError(f'Archivo de bloqueo inválido (symlink): {raw}')
    parent = raw.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / raw.name
    if target.is_symlink():
        raise ValueError(f'Archivo de bloqueo inválido (symlink): {target}')

    fd = os.open(target, os.O_RDWR | os.O_CREAT, 0o600)
    locked = False
    try:
        if fcntl is not None:
            flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if non_blocking else 0)
            fcntl.flock(fd, flags)
            locked = True
        elif msvcrt is not None:  # pragma: no cover - Windows
            os.lseek(fd, 0, os.SEEK_SET)
            if os.fstat(fd).st_size == 0:
                os.write(fd, b'0')
                os.lseek(fd, 0, os.SEEK_SET)
            mode = msvcrt.LK_NBLCK if non_blocking else msvcrt.LK_LOCK
            msvcrt.locking(fd, mode, 1)
            locked = True
        yield fd
    finally:
        if locked:
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                elif msvcrt is not None:  # pragma: no cover - Windows
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        os.close(fd)


def _validate_baloto_url(url: str) -> None:
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or host not in ALLOWED_BALOTO_HOSTS:
        raise UpstreamSyncError(f'URL oficial no autorizada: {url!r}.')


class _SafeBalotoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_baloto_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_html(url: str, timeout: int = 20, max_bytes: int = MAX_HTML_BYTES) -> str:
    _validate_baloto_url(url)
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    opener = urllib.request.build_opener(_SafeBalotoRedirectHandler())
    with opener.open(req, timeout=timeout) as resp:
        final_url = resp.geturl()
        if final_url:
            _validate_baloto_url(final_url)
        raw = resp.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise UpstreamSyncError('La respuesta de baloto.com supera el tamaño máximo permitido (2 MB).')
        return raw.decode('utf-8', errors='replace')


# ---------------------------------------------------------------------------
# Validación y manejo del historial oficial (CSV) y estadísticas
# ---------------------------------------------------------------------------

def validate_draw_record(record: dict) -> dict:
    raw_sorteo = record.get('sorteo')
    if type(raw_sorteo) is bool:
        raise ValueError('Sorteo inválido.')
    sorteo = integer(int(raw_sorteo), 'Sorteo', 1, 999999) if isinstance(raw_sorteo, (int, str)) else integer(raw_sorteo, 'Sorteo', 1, 999999)
    fecha = record.get('fecha')
    if not isinstance(fecha, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', fecha):
        raise ValueError(f'Sorteo #{sorteo}: fecha inválida ({fecha!r}), usa YYYY-MM-DD.')
    datetime.strptime(fecha, '%Y-%m-%d')

    baloto = sorted(int(x) for x in record.get('baloto', []))
    revancha = sorted(int(x) for x in record.get('revancha', []))
    if len(baloto) != 5 or len(set(baloto)) != 5 or not all(1 <= n <= 43 for n in baloto):
        raise ValueError(f'Sorteo #{sorteo}: Baloto debe tener 5 números únicos entre 1 y 43.')
    if len(revancha) != 5 or len(set(revancha)) != 5 or not all(1 <= n <= 43 for n in revancha):
        raise ValueError(f'Sorteo #{sorteo}: Revancha debe tener 5 números únicos entre 1 y 43.')
    sb_b = integer(int(record.get('sb_baloto', 0)), f'Sorteo #{sorteo} SB Baloto', 1, 16)
    sb_r = integer(int(record.get('sb_revancha', 0)), f'Sorteo #{sorteo} SB Revancha', 1, 16)
    return {
        'sorteo': sorteo,
        'fecha': fecha,
        'baloto': baloto,
        'sb_baloto': sb_b,
        'revancha': revancha,
        'sb_revancha': sb_r,
    }


def _clone_history_rows(rows: list[dict]) -> list[dict]:
    return [
        {
            'sorteo': r['sorteo'],
            'fecha': r['fecha'],
            'baloto': list(r['baloto']),
            'sb_baloto': r['sb_baloto'],
            'revancha': list(r['revancha']),
            'sb_revancha': r['sb_revancha'],
        }
        for r in rows
    ]


def load_history(path: Path = DEFAULT_HISTORY_PATH) -> list[dict]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        with _HISTORY_CACHE_LOCK:
            _HISTORY_CACHE.pop(resolved, None)
        return []

    st = resolved.stat()
    with _HISTORY_CACHE_LOCK:
        cached = _HISTORY_CACHE.get(resolved)
        if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
            return _clone_history_rows(cached[2])

    rows: list[dict] = []
    seen_sorteos: set[int] = set()
    try:
        with resolved.open('r', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or not all(col in reader.fieldnames for col in HISTORY_COLUMNS):
                raise ValueError(f'Encabezados inválidos en {resolved.name}.')
            for line_num, raw in enumerate(reader, 2):
                if not raw or all(v is None or not str(v).strip() for v in raw.values()):
                    continue
                if None in raw:
                    raise ValueError(f'Columnas sobrantes en {resolved.name} (línea {line_num}).')
                rec = validate_draw_record({
                    'sorteo': int(str(raw['sorteo']).strip()),
                    'fecha': str(raw['fecha']).strip(),
                    'baloto': [int(str(raw[f'b{i}']).strip()) for i in range(1, 6)],
                    'sb_baloto': int(str(raw['sb_baloto']).strip()),
                    'revancha': [int(str(raw[f'r{i}']).strip()) for i in range(1, 6)],
                    'sb_revancha': int(str(raw['sb_revancha']).strip()),
                })
                if rec['sorteo'] in seen_sorteos:
                    raise ValueError(f"Sorteo duplicado #{rec['sorteo']} en {resolved.name} (línea {line_num}).")
                seen_sorteos.add(rec['sorteo'])
                rows.append(rec)
    except (KeyError, TypeError, ValueError, csv.Error) as error:
        if isinstance(error, ValueError):
            raise
        raise ValueError(f'Error al leer {resolved.name}: {error}') from error

    rows.sort(key=lambda r: r['sorteo'])
    with _HISTORY_CACHE_LOCK:
        _HISTORY_CACHE[resolved] = (st.st_mtime_ns, st.st_size, _clone_history_rows(rows))
    return rows


def save_history(rows: list[dict], path: Path = DEFAULT_HISTORY_PATH) -> None:
    by_sorteo = {}
    for row in rows:
        clean = validate_draw_record(row)
        by_sorteo[clean['sorteo']] = clean
    ordered = [by_sorteo[k] for k in sorted(by_sorteo)]
    lines = [','.join(HISTORY_COLUMNS)]
    for r in ordered:
        vals = [str(r['sorteo']), r['fecha'], *map(str, r['baloto']), str(r['sb_baloto']),
                *map(str, r['revancha']), str(r['sb_revancha'])]
        lines.append(','.join(vals))
    atomic_write_text(path, '\n'.join(lines) + '\n', mode=0o644)
    resolved = Path(path).expanduser().resolve()
    try:
        st = resolved.stat()
        with _HISTORY_CACHE_LOCK:
            _HISTORY_CACHE[resolved] = (st.st_mtime_ns, st.st_size, _clone_history_rows(ordered))
    except OSError:
        pass


def compute_stats(rows: list[dict], game: str = 'baloto', last: int | None = None) -> dict:
    if game not in ('baloto', 'revancha', 'both'):
        raise ValueError("El parámetro 'game' debe ser 'baloto', 'revancha' o 'both'.")
    ordered = sorted(rows, key=lambda r: r['sorteo'])
    if last is not None:
        integer(last, 'Ventana de sorteos', 1, 100000)
        ordered = ordered[-last:]
    if not ordered:
        raise ValueError('No hay sorteos en el historial para calcular estadísticas.')

    draw_count = len(ordered)
    samples: list[tuple[list[int], int]] = []
    per_draw_sets: list[tuple[set[int], set[int]]] = []
    for r in ordered:
        if game == 'baloto':
            samples.append((r['baloto'], r['sb_baloto']))
            per_draw_sets.append((set(r['baloto']), {r['sb_baloto']}))
        elif game == 'revancha':
            samples.append((r['revancha'], r['sb_revancha']))
            per_draw_sets.append((set(r['revancha']), {r['sb_revancha']}))
        else:
            samples.append((r['baloto'], r['sb_baloto']))
            samples.append((r['revancha'], r['sb_revancha']))
            per_draw_sets.append((set(r['baloto']) | set(r['revancha']),
                                  {r['sb_baloto'], r['sb_revancha']}))

    m = len(samples)
    num_counts: Counter[int] = Counter()
    sb_counts: Counter[int] = Counter()
    pair_counts: Counter[tuple[int, int]] = Counter()
    odd_even_counts: Counter[str] = Counter()
    high_low_counts: Counter[str] = Counter()
    consecutive_counts: Counter[int] = Counter()
    sums = []

    for nums, sb in samples:
        sorted_nums = sorted(nums)
        num_counts.update(sorted_nums)
        sb_counts[sb] += 1
        sums.append(sum(sorted_nums))
        for a, b in combinations(sorted_nums, 2):
            pair_counts[(a, b)] += 1

        odds = sum(1 for x in sorted_nums if x % 2 != 0)
        evens = 5 - odds
        odd_even_counts[f'{odds}I-{evens}P'] += 1

        lows = sum(1 for x in sorted_nums if x <= 22)
        highs = 5 - lows
        high_low_counts[f'{lows}B-{highs}A'] += 1

        consec = sum(1 for i in range(4) if sorted_nums[i + 1] == sorted_nums[i] + 1)
        consecutive_counts[consec] += 1

    def lag_for(target: int, is_sb: bool = False) -> int:
        idx = 1 if is_sb else 0
        for offset, draw_sets in enumerate(reversed(per_draw_sets)):
            if target in draw_sets[idx]:
                return offset
        return draw_count

    exp_num = m * (5.0 / 43.0)
    sd_num = math.sqrt(m * (5.0 / 43.0) * (38.0 / 43.0))
    numbers = []
    chi2_raw = 0.0
    for n in range(1, 44):
        c = num_counts[n]
        chi2_raw += ((c - exp_num) ** 2) / exp_num
        z = (c - exp_num) / sd_num if sd_num > 0 else 0.0
        numbers.append({
            'number': n,
            'count': c,
            'expected': round(exp_num, 2),
            'z_score': round(z, 3),
            'draws_since_seen': lag_for(n, is_sb=False),
        })
    # Al extraer k=5 balotas sin reemplazo de N=43, E[sum((C_i - E)^2 / E)] = N - k = 38.
    # El factor (N - 1) / (N - k) = 42 / 38 ajusta el estadístico a chi^2 con gl = 42.
    chi2_num = chi2_raw * (42.0 / 38.0)

    exp_sb = m / 16.0
    sd_sb = math.sqrt(m * (1.0 / 16.0) * (15.0 / 16.0))
    superbalotas = []
    chi2_sb = 0.0
    for sb in range(1, 17):
        c = sb_counts[sb]
        chi2_sb += ((c - exp_sb) ** 2) / exp_sb
        z = (c - exp_sb) / sd_sb if sd_sb > 0 else 0.0
        superbalotas.append({
            'number': sb,
            'count': c,
            'expected': round(exp_sb, 2),
            'z_score': round(z, 3),
            'draws_since_seen': lag_for(sb, is_sb=True),
        })

    hot = sorted(numbers, key=lambda x: (-x['count'], x['number']))[:10]
    cold = sorted(numbers, key=lambda x: (x['count'], x['number']))[:10]
    overdue = sorted(numbers, key=lambda x: (-x['draws_since_seen'], x['number']))[:10]
    top_pairs = [
        {'pair': [a, b], 'count': cnt}
        for (a, b), cnt in sorted(pair_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:15]
    ]

    # Modelo Bayesiano Beta-Binomial (prior global + verosimilitud de últimos 50 sorteos)
    recent_w = min(50, m)
    recent_num_counts: Counter[int] = Counter()
    recent_sb_counts: Counter[int] = Counter()
    for r_nums, r_sb in samples[-recent_w:]:
        recent_num_counts.update(r_nums)
        recent_sb_counts[r_sb] += 1

    prior_total_num = m * 5
    recent_total_num = recent_w * 5
    bayesian_num_scores = {}
    for n in range(1, 44):
        c_h = num_counts[n]
        c_r = recent_num_counts[n]
        alpha_post = c_h + 1 + c_r
        beta_post = (prior_total_num - c_h + 1) + (recent_total_num - c_r)
        bayesian_num_scores[n] = alpha_post / (alpha_post + beta_post)
    sum_b = sum(bayesian_num_scores.values()) or 1.0
    bayesian_num_norm = {n: (score / sum_b) * 5.0 for n, score in bayesian_num_scores.items()}

    prior_total_sb = m
    recent_total_sb = recent_w
    bayesian_sb_scores = {}
    for sb in range(1, 17):
        c_h = sb_counts[sb]
        c_r = recent_sb_counts[sb]
        alpha_post = c_h + 1 + c_r
        beta_post = (prior_total_sb - c_h + 1) + (recent_total_sb - c_r)
        bayesian_sb_scores[sb] = alpha_post / (alpha_post + beta_post)
    sum_sb = sum(bayesian_sb_scores.values()) or 1.0
    bayesian_sb_norm = {sb: score / sum_sb for sb, score in bayesian_sb_scores.items()}

    top_bayesian_numbers = [
        {'number': n, 'score': round(bayesian_num_norm[n], 4), 'count': num_counts[n], 'recent_count': recent_num_counts[n]}
        for n in sorted(bayesian_num_norm, key=lambda x: -bayesian_num_norm[x])[:10]
    ]
    top_bayesian_sb = [
        {'superbalota': sb, 'score': round(bayesian_sb_norm[sb], 4), 'count': sb_counts[sb], 'recent_count': recent_sb_counts[sb]}
        for sb in sorted(bayesian_sb_norm, key=lambda x: -bayesian_sb_norm[x])[:5]
    ]

    sums_sorted = sorted(sums)
    q25 = sums_sorted[len(sums_sorted) // 4]
    q75 = sums_sorted[(3 * len(sums_sorted)) // 4]
    stdev_val = round(statistics.stdev(sums), 2) if len(sums) > 1 else 0.0

    return {
        'game': game,
        'draw_count': draw_count,
        'sample_draws': m,
        'from_sorteo': ordered[0]['sorteo'],
        'to_sorteo': ordered[-1]['sorteo'],
        'from_fecha': ordered[0]['fecha'],
        'to_fecha': ordered[-1]['fecha'],
        'next_sorteo': ordered[-1]['sorteo'] + 1,
        'chi2_numbers': {'chi2': round(chi2_num, 3), 'df': 42},
        'chi2_superbalota': {'chi2': round(chi2_sb, 3), 'df': 15},
        'numbers': numbers,
        'superbalotas': superbalotas,
        'hot_numbers': hot,
        'cold_numbers': cold,
        'overdue_numbers': overdue,
        'top_pairs': top_pairs,
        'odd_even': {
            'distribution': dict(sorted(odd_even_counts.items(), key=lambda kv: -kv[1])),
            'percentages': {k: round(v / m * 100, 2) for k, v in sorted(odd_even_counts.items(), key=lambda kv: -kv[1])},
            'most_common': max(odd_even_counts, key=odd_even_counts.get) if odd_even_counts else '3I-2P',
        },
        'high_low': {
            'distribution': dict(sorted(high_low_counts.items(), key=lambda kv: -kv[1])),
            'percentages': {k: round(v / m * 100, 2) for k, v in sorted(high_low_counts.items(), key=lambda kv: -kv[1])},
            'most_common': max(high_low_counts, key=high_low_counts.get) if high_low_counts else '3B-2A',
        },
        'consecutive_pairs': {
            'distribution': dict(sorted(consecutive_counts.items())),
            'percentages': {str(k): round(v / m * 100, 2) for k, v in sorted(consecutive_counts.items())},
            'at_least_one_pct': round(sum(v for k, v in consecutive_counts.items() if k > 0) / m * 100, 2),
            'none_pct': round(consecutive_counts[0] / m * 100, 2) if consecutive_counts else 0.0,
        },
        'bayesian': {
            'recent_window': recent_w,
            'top_numbers': top_bayesian_numbers,
            'top_superbalotas': top_bayesian_sb,
            'all_number_scores': {n: round(bayesian_num_norm[n], 4) for n in range(1, 44)},
            'all_sb_scores': {sb: round(bayesian_sb_norm[sb], 4) for sb in range(1, 17)},
        },
        'sum_stats': {
            'min': min(sums),
            'max': max(sums),
            'mean': round(statistics.mean(sums), 2),
            'median': round(statistics.median(sums), 2),
            'std': stdev_val,
            'q25': q25,
            'q75': q75,
        },
    }


def _weighted_sample_without_replacement(
    population: list[int],
    weights: dict[int, float],
    k: int,
    rng: random.Random,
) -> list[int]:
    pop = list(population)
    w = [max(weights.get(x, 1.0), 1e-6) for x in pop]
    chosen = []
    for _ in range(k):
        pick = rng.choices(pop, weights=w, k=1)[0]
        chosen.append(pick)
        idx = pop.index(pick)
        pop.pop(idx)
        w.pop(idx)
    return sorted(chosen)


def generate_suggested_lines(
    rows: list[dict],
    count: int = 1,
    strategy: str = 'bayesian',
    game: str = 'baloto',
    combo_label: str | None = None,
    rng: random.Random | None = None,
) -> dict:
    if strategy not in SUGGESTION_STRATEGIES:
        raise ValueError(
            f"Estrategia desconocida {strategy!r}. Válidas: {', '.join(sorted(SUGGESTION_STRATEGIES))}."
        )
    integer(count, 'Cantidad de sugerencias', 1, 50)
    st = compute_stats(rows, game=game)
    r = rng or random.Random()

    num_weights: dict[int, float] = {}
    sb_weights: dict[int, float] = {}

    if strategy == 'bayesian':
        all_num_s = st['bayesian']['all_number_scores']
        for n in range(1, 44):
            num_weights[n] = max(all_num_s.get(n, 0.1), 0.01)
        all_sb_s = st['bayesian']['all_sb_scores']
        for sb in range(1, 17):
            sb_weights[sb] = max(all_sb_s.get(sb, 0.05), 0.01)

    elif strategy == 'hot':
        for item in st['numbers']:
            num_weights[item['number']] = max(0.05, 1.0 + max(item['z_score'], -0.9))
        for item in st['superbalotas']:
            sb_weights[item['number']] = max(0.05, 1.0 + max(item['z_score'], -0.9))

    elif strategy == 'cold':
        for item in st['numbers']:
            num_weights[item['number']] = max(0.05, 1.0 + (item['draws_since_seen'] / 10.0))
        for item in st['superbalotas']:
            sb_weights[item['number']] = max(0.05, 1.0 + (item['draws_since_seen'] / 10.0))

    elif strategy == 'anti_collision':
        for n in range(1, 44):
            if n > 31:
                num_weights[n] = 3.5
            elif n <= 12:
                num_weights[n] = 0.5
            elif n in (7, 11, 13, 21):
                num_weights[n] = 0.3
            else:
                num_weights[n] = 1.0
        for sb in range(1, 17):
            sb_weights[sb] = 2.5 if sb > 12 else 0.8

    else:  # balanced
        for n in range(1, 44):
            num_weights[n] = 1.0
        for sb in range(1, 17):
            sb_weights[sb] = 1.0

    lines: list[dict] = []
    lines_text: list[str] = []
    seen_combos = set()

    q25 = st['sum_stats'].get('q25', 90)
    q75 = st['sum_stats'].get('q75', 130)

    for i in range(1, count + 1):
        best_pick = None
        for _ in range(1000):
            pick = _weighted_sample_without_replacement(list(range(1, 44)), num_weights, 5, r)

            if strategy in ('bayesian', 'balanced', 'anti_collision'):
                if all(x <= 31 for x in pick):
                    continue

            if strategy == 'anti_collision':
                if sum(1 for x in pick if x > 31) < 2:
                    continue
                gaps = [pick[j + 1] - pick[j] for j in range(4)]
                if len(set(gaps)) <= 1:
                    continue

            if strategy == 'balanced':
                odds = sum(1 for x in pick if x % 2 != 0)
                if odds not in (2, 3):
                    continue
                lows = sum(1 for x in pick if x <= 22)
                if lows not in (2, 3):
                    continue
                s = sum(pick)
                if not (q25 <= s <= q75):
                    continue
                consec = sum(1 for j in range(4) if pick[j + 1] == pick[j] + 1)
                if consec > 1:
                    continue

            key = tuple(pick)
            if key not in seen_combos:
                seen_combos.add(key)
                best_pick = pick
                break

        if best_pick is None:
            best_pick = _weighted_sample_without_replacement(list(range(1, 44)), num_weights, 5, r)

        sb_weights_list = [sb_weights.get(x, 1.0) for x in range(1, 17)]
        sb_pick = r.choices(list(range(1, 17)), weights=sb_weights_list, k=1)[0]

        lbl = combo_label or (f"Sugerencia {i}" if count > 1 else "Sugerencia")
        entry = {
            'numbers': best_pick,
            'extra': sb_pick,
            'combo': lbl,
            'sum': sum(best_pick),
            'odd_even': f"{sum(1 for x in best_pick if x % 2 != 0)}I-{sum(1 for x in best_pick if x % 2 == 0)}P",
        }
        lines.append(entry)

        formatted_nums = ' '.join(f'{n:02d}' for n in best_pick)
        lines_text.append(f'{lbl}: {formatted_nums} | {sb_pick:02d}')

    return {
        'strategy': strategy,
        'strategy_description': SUGGESTION_STRATEGIES[strategy],
        'count': count,
        'game': game,
        'lines': lines,
        'text': '\n'.join(lines_text),
        'disclaimer': (
            'Todos los sorteos son eventos independientes con probabilidad uniforme. '
            'Las sugerencias se basan en modelado estadístico y teoría de juegos anti-colisión sin garantizar premio.'
        ),
    }


# ---------------------------------------------------------------------------
# Parsers del HTML oficial de baloto.com y cálculo real de premios COP
# ---------------------------------------------------------------------------

def _parse_sorteo_token(raw_token: str) -> int:
    digits = raw_token.replace('.', '').strip()
    if not digits.isdigit():
        raise UpstreamSyncError(f'Número de sorteo inválido en HTML oficial: {raw_token!r}.')
    return integer(int(digits), 'Sorteo', 1, 999999)


def parse_spanish_date(html: str) -> str:
    text = html_lib.unescape(re.sub(r'<[^>]+>', ' ', html))
    m = re.search(r'\b(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚáéíóúñÑ]+)\s+de\s+(\d{4})\b', text)
    if m:
        day, month_name, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        month = MESES.get(month_name)
        if month is not None:
            iso = f'{year:04d}-{month:02d}-{day:02d}'
            try:
                datetime.strptime(iso, '%Y-%m-%d')
                return iso
            except ValueError as err:
                raise UpstreamSyncError(f'Fecha oficial inválida ({iso}) en baloto.com.') from err
    m_slash = re.search(r'\b(\d{2})/(\d{2})/(\d{4})\b', text)
    if m_slash:
        d, mo, y = map(int, m_slash.groups())
        iso = f'{y:04d}-{mo:02d}-{d:02d}'
        try:
            datetime.strptime(iso, '%Y-%m-%d')
            return iso
        except ValueError as err:
            raise UpstreamSyncError(f'Fecha oficial inválida ({iso}) en baloto.com.') from err
    raise UpstreamSyncError('No se pudo leer la fecha oficial del sorteo en baloto.com.')


def parse_latest_results_html(html: str) -> dict:
    m_sorteo = re.search(r'Resultado\s+sorteo\s*#?\s*(\d{1,3}(?:\.\d{3})+|\d+)', html, re.I)
    balls = re.findall(r'class=["\'](?:[^"\']*\b)?(?:yellow-ball|red-ball)\b[^"\']*["\']>\s*(\d+)\s*</div>', html)
    if not m_sorteo or len(balls) < 12:
        raise UpstreamSyncError('Formato inesperado en baloto.com/resultados.')
    sorteo = _parse_sorteo_token(m_sorteo.group(1))
    fecha = parse_spanish_date(html)
    baloto = sorted(int(x) for x in balls[0:5])
    sb_b = int(balls[5])
    revancha = sorted(int(x) for x in balls[6:11])
    sb_r = int(balls[11])
    try:
        return validate_draw_record({
            'sorteo': sorteo,
            'fecha': fecha,
            'baloto': baloto,
            'sb_baloto': sb_b,
            'revancha': revancha,
            'sb_revancha': sb_r,
        })
    except ValueError as err:
        raise UpstreamSyncError(str(err)) from err


def parse_prize_table_html(html: str) -> dict[str, int]:
    start = html.find('PREMIO TOTAL')
    if start == -1:
        raise UpstreamSyncError('No se encontró la tabla oficial de premios por categoría.')
    candidates = [
        pos for pos in (
            html.find('</tbody>', start),
            html.find('</table>', start),
            html.find('next-contest', start),
        )
        if pos != -1
    ]
    if not candidates:
        raise UpstreamSyncError('No se encontró el cierre de la tabla oficial de premios.')
    chunk = html[start:min(candidates)]
    rows = re.findall(r'<tr\b[^>]*\bbr-header-left\b[^>]*>(.*?)</tr>', chunk, re.S)
    if len(rows) != 8:
        raise UpstreamSyncError(f'Se esperaban 8 categorías oficiales de premio; llegaron {len(rows)}.')
    table: dict[str, int] = {}
    for key, row in zip(PRIZE_CATEGORY_KEYS, rows):
        vals = [
            html_lib.unescape(v).strip()
            for v in re.findall(r'<td\b[^>]*\bdark-blue\b[^>]*>([^<]*)</td>', row)
        ]
        if len(vals) != 3:
            raise UpstreamSyncError('Fila de premio con formato inesperado en baloto.com.')
        digits = re.sub(r'[^\d]', '', vals[2])
        if not digits:
            raise UpstreamSyncError(f'Premio vacío para la categoría {key} en baloto.com.')
        try:
            prize_per_winner = integer(int(digits), f'Premio {key}', 0, 1_000_000_000_000)
        except ValueError as err:
            raise UpstreamSyncError(str(err)) from err
        table[key] = prize_per_winner
    return table


def is_prize_table_settled(prize_table: dict[str, int]) -> bool:
    """Verifica que el escrutinio oficial ya fue liquidado (reembolso fijo SB > $0)."""
    if not isinstance(prize_table, dict):
        return False
    refund = int(prize_table.get('0 o 1 + SB', prize_table.get('0+SB', 0)))
    return refund > 0


def parse_detail_draw_html(html: str, expected_sorteo: int | None = None,
                           require_prizes: bool = True) -> dict:
    bg_match = re.search(r'id=["\']balotoBgNew["\']', html)
    header_html = html[bg_match.start():] if bg_match else html

    m_sorteo = re.search(r'sorteo\s*#?\s*(\d{1,3}(?:\.\d{3})+|\d+)', header_html, re.I)
    sorteo = _parse_sorteo_token(m_sorteo.group(1)) if m_sorteo else expected_sorteo
    if sorteo is None:
        raise UpstreamSyncError('No se pudo determinar el número de sorteo en el detalle.')
    if expected_sorteo is not None and sorteo != expected_sorteo:
        raise DrawNotFoundError(f'El detalle devolvió sorteo #{sorteo} en lugar de #{expected_sorteo}.')

    balls = re.findall(
        r'class=["\'](?:[^"\']*\b)?(?:yellow-ball|red-ball)\b[^"\']*["\']>\s*(\d+)\s*</div>',
        header_html,
    )
    if len(balls) < 6:
        raise UpstreamSyncError(f'No se encontraron las 6 balotas del sorteo #{sorteo}.')
    nums = sorted(int(x) for x in balls[0:5])
    if len(set(nums)) != 5 or not all(1 <= n <= 43 for n in nums):
        raise UpstreamSyncError(f'Balotas inválidas en el detalle del sorteo #{sorteo}.')
    try:
        sb = integer(int(balls[5]), f'Superbalota #{sorteo}', 1, 16)
    except ValueError as err:
        raise UpstreamSyncError(str(err)) from err

    fecha = parse_spanish_date(header_html)
    prizes = parse_prize_table_html(html) if require_prizes else {}
    return {'sorteo': sorteo, 'fecha': fecha, 'numbers': nums, 'sb': sb, 'prizes': prizes}


def _plan_history_sync(existing: list[dict], fetcher=fetch_html,
                       from_draw: int | None = None) -> tuple[list[dict], list[int], dict]:
    by_sorteo = {r['sorteo']: r for r in existing}
    latest = parse_latest_results_html(fetcher(RESULTADOS_URL))

    if from_draw is not None:
        start = integer(from_draw, 'Sorteo inicial', 1, latest['sorteo'])
        force_refresh = True
    elif by_sorteo:
        start = max(by_sorteo) + 1
        force_refresh = False
        if latest['sorteo'] - start + 1 > MAX_AUTO_SYNC_SPAN:
            raise UpstreamSyncError(
                f"El salto automático de sorteos ({start}..{latest['sorteo']}) excede el máximo de "
                f'{MAX_AUTO_SYNC_SPAN}; usa --from-draw para sincronizar rangos grandes.'
            )
    else:
        start = latest['sorteo']
        force_refresh = False

    added: list[int] = []
    for s in range(start, latest['sorteo'] + 1):
        if not force_refresh and s in by_sorteo:
            continue
        if s == latest['sorteo']:
            record = latest
        else:
            try:
                det_b = parse_detail_draw_html(
                    fetcher(DETALLE_BALOTO_URL.format(sorteo=s)), expected_sorteo=s, require_prizes=False
                )
            except DrawNotFoundError:
                # baloto.com redirige números inexistentes al último sorteo: no hubo sorteo con ese número.
                continue
            det_r = parse_detail_draw_html(
                fetcher(DETALLE_REVANCHA_URL.format(sorteo=s)), expected_sorteo=s, require_prizes=False
            )
            try:
                record = validate_draw_record({
                    'sorteo': s,
                    'fecha': det_b['fecha'],
                    'baloto': det_b['numbers'],
                    'sb_baloto': det_b['sb'],
                    'revancha': det_r['numbers'],
                    'sb_revancha': det_r['sb'],
                })
            except ValueError as err:
                raise UpstreamSyncError(str(err)) from err
        if s not in by_sorteo or by_sorteo[s] != record:
            added.append(s)
        by_sorteo[s] = record

    merged = [by_sorteo[k] for k in sorted(by_sorteo)]
    return merged, added, latest


def sync_history(path: Path = DEFAULT_HISTORY_PATH, fetcher=fetch_html,
                 from_draw: int | None = None) -> dict:
    resolved = Path(path).expanduser().resolve()
    existing = load_history(resolved)
    merged, added, latest = _plan_history_sync(existing, fetcher=fetcher, from_draw=from_draw)

    if added or not resolved.exists():
        lock_file = resolved.parent / f'.{resolved.name}.lock'
        try:
            with file_lock(lock_file):
                disk_rows = load_history(resolved)
                by_sorteo = {r['sorteo']: r for r in disk_rows}
                for r in merged:
                    if from_draw is not None or r['sorteo'] not in by_sorteo:
                        by_sorteo[r['sorteo']] = r
                save_history(list(by_sorteo.values()), resolved)
                total_draws = len(by_sorteo)
        except PermissionError:
            save_history(merged, resolved)
            total_draws = len(merged)
    else:
        total_draws = len(merged)

    return {
        'latest_sorteo': latest['sorteo'],
        'latest_fecha': latest['fecha'],
        'added': added,
        'total_draws': total_draws,
    }


def prize_for_hits(prize_table: dict[str, int], hits: int, sb_match: bool) -> tuple[int, str | None]:
    integer(hits, 'Aciertos', 0, 5)
    if type(sb_match) is not bool:
        raise ValueError('El indicador de Superbalota debe ser booleano.')
    if hits >= 3:
        key = f'{hits}+SB' if sb_match else f'{hits}'
        return int(prize_table.get(key, 0)), key
    if sb_match:
        if hits == 2:
            return int(prize_table.get('2+SB', 0)), '2+SB'
        val = int(prize_table.get('0 o 1 + SB', prize_table.get('0+SB', 0)))
        return val, '0 o 1 + SB'
    return 0, None


# ---------------------------------------------------------------------------
# Validación de jugadas / tiquetes, agrupación de boletas y persistencia local
# ---------------------------------------------------------------------------

def group_into_slips(lines: list[dict], draw_count: int, price_per_line: int, mode: str) -> list[dict]:
    """Agrupa líneas en boletas de máximo 5 paneles (A–E), consolidando etiquetas de combo iguales."""
    buckets: list[tuple[str | None, list[dict]]] = []
    open_idx_by_key: dict[str | None, int] = {}

    for line in lines:
        combo = line.get('combo')
        idx = open_idx_by_key.get(combo)
        if idx is not None and len(buckets[idx][1]) < len(PANEL_LETTERS):
            buckets[idx][1].append(line)
        else:
            open_idx_by_key[combo] = len(buckets)
            buckets.append((combo, [line]))

    slips: list[dict] = []
    for slip_idx, (combo, group) in enumerate(buckets, 1):
        panels = []
        for panel_letter, line in zip(PANEL_LETTERS, group):
            panels.append({
                'panel': panel_letter,
                'numbers': list(line['numbers']),
                'numbers_formatted': [f'{n:02d}' for n in line['numbers']],
                'superbalota': line['extra'],
                'superbalota_formatted': f"{line['extra']:02d}",
                'revancha': mode == 'baloto_revancha',
            })
        slip_cost = len(panels) * draw_count * price_per_line
        slips.append({
            'slip_index': slip_idx,
            'combo': combo or f'Boleta {slip_idx}',
            'panel_count': len(panels),
            'panels': panels,
            'slip_cost_cop': slip_cost,
            'slip_cost_formatted': fmt_cop(slip_cost),
        })
    return slips


def parse_lines(value: str) -> list[dict]:
    if not isinstance(value, str) or len(value) > 40000:
        raise ValueError('Introduce las líneas como texto (máximo 40.000 caracteres).')
    lines, seen = [], set()
    for index, raw in enumerate(value.splitlines(), 1):
        clean = raw.strip()
        if not clean or clean.startswith('#'):
            continue
        combo = None
        if ':' in clean:
            prefix, rest = clean.split(':', 1)
            combo = prefix.strip()
            if not combo or len(combo) > 24 or not COMBO_LABEL_RE.fullmatch(combo):
                raise ValueError(
                    f'Línea {index}: etiqueta de combo inválida (máx. 24 caracteres alfanuméricos, espacios, ., #, -).'
                )
            clean = rest.strip()
        parts = clean.split('|')
        if len(parts) != 2:
            raise ValueError(f'Línea {index}: usa el formato "01 02 03 04 05 | 06".')
        pieces = re.split(r'[\s,\-]+', parts[0].strip())
        if len(pieces) != 5 or not all(re.fullmatch(r'\d{1,2}', p) for p in pieces):
            raise ValueError(f'Línea {index}: se necesitan exactamente cinco números.')
        numbers = sorted(map(int, pieces))
        if len(set(numbers)) != 5 or not all(1 <= n <= 43 for n in numbers):
            raise ValueError(f'Línea {index}: cinco números distintos entre 1 y 43.')
        sb_str = parts[1].strip()
        if not re.fullmatch(r'\d{1,2}', sb_str):
            raise ValueError(f'Línea {index}: Superbalota inválida.')
        extra = integer(int(sb_str), f'Línea {index}, Superbalota', 1, 16)
        key = tuple(numbers) + (extra,)
        if key in seen:
            raise ValueError(f'Línea {index}: combinación duplicada en el mismo tiquete.')
        seen.add(key)
        entry: dict = {'numbers': numbers, 'extra': extra}
        if combo:
            entry['combo'] = combo
        lines.append(entry)
    if not 1 <= len(lines) <= 500:
        raise ValueError('Introduce entre 1 y 500 líneas por tiquete.')
    return lines


def _default_next_draw(history_path: Path = DEFAULT_HISTORY_PATH) -> int:
    try:
        rows = load_history(history_path)
    except ValueError:
        rows = []
    return (rows[-1]['sorteo'] + 1) if rows else 2729


def make_ticket(payload: dict, clock: datetime, default_start_draw: int | None = None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('La jugada debe enviarse como objeto JSON.')
    allowed = {
        'label', 'mode', 'start_draw', 'draw_count',
        'price_per_line', 'already_purchased', 'lines',
    }
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"Campos no reconocidos: {', '.join(sorted(unknown))}.")

    label = payload.get('label', 'Mi tiquete Baloto')
    if not isinstance(label, str) or not label.strip() or len(label.strip()) > 80:
        raise ValueError('El nombre del tiquete debe tener entre 1 y 80 caracteres.')

    mode = payload.get('mode', 'baloto_revancha')
    if mode not in DEFAULT_PRICES:
        raise ValueError("La modalidad debe ser 'baloto_revancha' o 'baloto'.")

    raw_start = payload.get('start_draw')
    if raw_start is None:
        start_draw = default_start_draw if default_start_draw is not None else _default_next_draw()
    else:
        start_draw = integer(raw_start, 'Sorteo inicial', 1, 999999)
    draw_count = integer(payload.get('draw_count', 1), 'Cantidad de sorteos', 1, 52)

    raw_price = payload.get('price_per_line')
    price_per_line = (DEFAULT_PRICES[mode] if raw_price is None
                      else integer(raw_price, 'Precio por línea (COP)', 1000, 1_000_000))

    already_purchased = payload.get('already_purchased', False)
    if type(already_purchased) is not bool:
        raise ValueError("El campo 'already_purchased' debe ser booleano.")

    lines = parse_lines(payload.get('lines'))
    if len(lines) * draw_count > 5000:
        raise ValueError('El máximo permitido es 5.000 jugadas por registro.')

    cost = len(lines) * draw_count * price_per_line
    status = 'active' if already_purchased else 'cart'

    return {
        'id': uuid.uuid4().hex,
        'label': label.strip(),
        'mode': mode,
        'price_per_line': price_per_line,
        'start_draw': start_draw,
        'draw_count': draw_count,
        'lines': lines,
        'cost': cost,
        'status': status,
        'created_at': clock.isoformat(),
        'confirmed_at': clock.isoformat() if already_purchased else None,
        'draws': [
            {'index': i + 1, 'sorteo': start_draw + i, 'result': None}
            for i in range(draw_count)
        ],
    }


def evaluate_ticket_draw(ticket: dict, official: dict, checked_at: str) -> dict:
    baloto_set = set(official['baloto']['numbers'])
    baloto_sb = official['baloto']['sb']
    baloto_prizes = official['baloto']['prizes']

    has_revancha = ticket['mode'] == 'baloto_revancha'
    rev_set = set(official['revancha']['numbers']) if has_revancha else set()
    rev_sb = official['revancha']['sb'] if has_revancha else 0
    rev_prizes = official['revancha']['prizes'] if has_revancha else {}

    line_results = []
    total_prize = 0
    for idx, line in enumerate(ticket['lines'], 1):
        nums_set = set(line['numbers'])
        b_hits = len(nums_set & baloto_set)
        b_sb = line['extra'] == baloto_sb
        b_prize, b_cat = prize_for_hits(baloto_prizes, b_hits, b_sb)

        r_hits = len(nums_set & rev_set) if has_revancha else 0
        r_sb = (line['extra'] == rev_sb) if has_revancha else False
        r_prize, r_cat = prize_for_hits(rev_prizes, r_hits, r_sb) if has_revancha else (0, None)

        line_total = b_prize + r_prize
        total_prize += line_total
        item = {
            'line_index': idx,
            'baloto_hits': b_hits,
            'baloto_sb': b_sb,
            'baloto_category': b_cat,
            'baloto_prize_cop': b_prize,
            'revancha_hits': r_hits,
            'revancha_sb': r_sb,
            'revancha_category': r_cat,
            'revancha_prize_cop': r_prize,
            'prize_cop': line_total,
        }
        if 'combo' in line:
            item['combo'] = line['combo']
        line_results.append(item)

    return {
        'fecha': official['fecha'],
        'baloto': {'numbers': list(official['baloto']['numbers']), 'extra': baloto_sb},
        'revancha': ({'numbers': list(official['revancha']['numbers']), 'extra': rev_sb}
                     if has_revancha and official.get('revancha') else None),
        'lines': line_results,
        'prize_cop': total_prize,
        'checked_at': checked_at,
    }


def _ticket_summary_fields(ticket: dict) -> dict:
    won = sum(d['result']['prize_cop'] for d in ticket['draws'] if d['result'] is not None)
    played_draws = sum(1 for d in ticket['draws'] if d['result'] is not None)
    spent = 0 if ticket['status'] == 'cart' else ticket['cost']
    return {
        'won': won,
        'spent': spent,
        'balance': won - spent,
        'played_draws': played_draws,
        'pending_draws': len(ticket['draws']) - played_draws,
        'end_draw': ticket['start_draw'] + ticket['draw_count'] - 1,
        'url': f"/seguimiento/{ticket['id']}",
    }


def public_ticket(ticket: dict) -> dict:
    out = copy.deepcopy(ticket)
    out.update(_ticket_summary_fields(out))
    out['slips'] = group_into_slips(
        out['lines'],
        draw_count=out['draw_count'],
        price_per_line=out['price_per_line'],
        mode=out['mode'],
    )
    return out


def _validate_state_dict(state: object) -> dict:
    if not isinstance(state, dict):
        raise ValueError('Formato de estado local no compatible; respalda state.json antes de migrar.')
    if (state.get('version') != 2
            or not isinstance(state.get('tickets'), dict)
            or not isinstance(state.get('official_draws'), dict)):
        raise ValueError('Formato de estado local no compatible; respalda state.json antes de migrar.')
    for key, ticket in state['tickets'].items():
        if (not isinstance(ticket, dict)
                or ticket.get('id') != key
                or ticket.get('mode') not in DEFAULT_PRICES
                or ticket.get('status') not in ('cart', 'active', 'complete')
                or not isinstance(ticket.get('lines'), list)
                or not isinstance(ticket.get('draws'), list)):
            raise ValueError(f'Tiquete corrupto en state.json ({key!r}).')
    return state


class Store:
    def __init__(self, directory: Path | str, history_path: Path = DEFAULT_HISTORY_PATH):
        self.directory = _ensure_private_dir(Path(directory))
        self.path = self.directory / 'state.json'
        self.lock_path = self.directory / '.store.lock'
        self.history_path = Path(history_path).expanduser().resolve()
        fallback_history = self.directory / 'historial_baloto.csv'
        if fallback_history.exists() and self.history_path == DEFAULT_HISTORY_PATH.resolve():
            try:
                if len(load_history(fallback_history)) > len(load_history(self.history_path)):
                    self.history_path = fallback_history
            except ValueError:
                pass
        self.mutex = threading.RLock()
        self.sync_mutex = threading.Lock()
        self._tx_depth = 0
        self._state_sig: tuple[int, int] | None = None
        self.state = self._load_state_from_disk()

    def _load_state_from_disk(self) -> dict:
        if self.path.is_symlink():
            raise ValueError(f'state.json no puede ser un enlace simbólico: {self.path}')
        if not self.path.exists():
            self._state_sig = None
            return {'version': 2, 'tickets': {}, 'official_draws': {}, 'last_sync_at': None}
        st = self.path.stat()
        raw = json.loads(self.path.read_text(encoding='utf-8'))
        validated = _validate_state_dict(raw)
        self._state_sig = (st.st_mtime_ns, st.st_size)
        return validated

    def _reload_if_changed(self) -> None:
        if not self.path.exists():
            return
        try:
            st = self.path.stat()
        except OSError:
            return
        sig = (st.st_mtime_ns, st.st_size)
        if sig != self._state_sig:
            self.state = self._load_state_from_disk()

    def save(self) -> None:
        atomic_write_text(self.path, json.dumps(self.state, ensure_ascii=False, indent=2), mode=0o600)
        try:
            st = self.path.stat()
            self._state_sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            pass

    @contextmanager
    def transaction(self):
        with self.mutex:
            outermost = self._tx_depth == 0
            if outermost:
                with file_lock(self.lock_path):
                    self._reload_if_changed()
                    before = copy.deepcopy(self.state)
                    self._tx_depth += 1
                    try:
                        yield
                    except Exception:
                        self.state = before
                        raise
                    finally:
                        self._tx_depth -= 1
            else:
                before = copy.deepcopy(self.state)
                self._tx_depth += 1
                try:
                    yield
                except Exception:
                    self.state = before
                    raise
                finally:
                    self._tx_depth -= 1

    def last_sync_at(self) -> str | None:
        with self.mutex:
            self._reload_if_changed()
            return self.state.get('last_sync_at')

    def next_suggested_draw(self) -> int:
        rows = load_history(self.history_path)
        return (rows[-1]['sorteo'] + 1) if rows else 2729

    def create(self, payload: dict, clock: datetime | None = None) -> dict:
        with self.transaction():
            ts = clock or now()
            ticket = make_ticket(payload, ts, default_start_draw=self.next_suggested_draw())
            self.state['tickets'][ticket['id']] = ticket
            self._evaluate_cached(ticket, ts)
            self.save()
            return public_ticket(ticket)

    def action(self, key: str, action: str, clock: datetime | None = None,
               start_draw: int | None = None) -> dict:
        with self.transaction():
            ts = clock or now()
            ticket = self.state['tickets'][key]
            if action == 'confirm':
                if ticket['status'] == 'cart':
                    if start_draw is not None:
                        clean_start = integer(start_draw, 'Sorteo inicial', 1, 999999)
                        ticket['start_draw'] = clean_start
                        ticket['draws'] = [
                            {'index': i + 1, 'sorteo': clean_start + i, 'result': None}
                            for i in range(ticket['draw_count'])
                        ]
                    ticket['status'] = 'active'
                    ticket['confirmed_at'] = ts.isoformat()
                elif start_draw is not None and start_draw != ticket['start_draw']:
                    raise ValueError('No se puede cambiar el sorteo inicial de un tiquete ya confirmado.')
            else:
                raise ValueError('Acción desconocida.')
            self._evaluate_cached(ticket, ts)
            self.save()
            return public_ticket(ticket)

    def delete(self, key: str) -> None:
        with self.transaction():
            if key not in self.state['tickets']:
                raise KeyError(key)
            del self.state['tickets'][key]
            self.save()

    def get(self, key: str) -> dict:
        with self.mutex:
            self._reload_if_changed()
            return public_ticket(self.state['tickets'][key])

    def list(self) -> list[dict]:
        with self.mutex:
            self._reload_if_changed()
            items = []
            for t in reversed(list(self.state['tickets'].values())):
                summary = _ticket_summary_fields(t)
                items.append({
                    'id': t['id'],
                    'label': t['label'],
                    'mode': t['mode'],
                    'status': t['status'],
                    'start_draw': t['start_draw'],
                    'end_draw': summary['end_draw'],
                    'cost': t['cost'],
                    'won': summary['won'],
                    'balance': summary['balance'],
                    'played_draws': summary['played_draws'],
                    'draw_count': t['draw_count'],
                    'url': summary['url'],
                })
            return items

    def _is_draw_ready_for_ticket(self, cached: dict | None, need_revancha: bool) -> bool:
        if not cached or not isinstance(cached.get('baloto'), dict):
            return False
        if not is_prize_table_settled(cached['baloto'].get('prizes', {})):
            return False
        if need_revancha:
            rev = cached.get('revancha')
            if not isinstance(rev, dict) or not is_prize_table_settled(rev.get('prizes', {})):
                return False
        return True

    def _evaluate_cached(self, ticket: dict, ts: datetime) -> bool:
        if ticket['status'] == 'cart':
            return False
        need_rev = ticket['mode'] == 'baloto_revancha'
        changed = False
        for d in ticket['draws']:
            if d['result'] is not None:
                continue
            cached = self.state['official_draws'].get(str(d['sorteo']))
            if not self._is_draw_ready_for_ticket(cached, need_rev):
                continue
            d['result'] = evaluate_ticket_draw(ticket, cached, ts.isoformat())
            changed = True
        if all(d['result'] is not None for d in ticket['draws']):
            ticket['status'] = 'complete'
        return changed

    def _fetch_official_draw_entry(self, sorteo: int, need_revancha: bool,
                                   existing_cached: dict | None, fetcher) -> dict | None:
        baloto_part = None
        fecha = existing_cached.get('fecha') if existing_cached else None
        if (existing_cached
                and isinstance(existing_cached.get('baloto'), dict)
                and is_prize_table_settled(existing_cached['baloto'].get('prizes', {}))):
            baloto_part = existing_cached['baloto']

        if baloto_part is None:
            det_b = parse_detail_draw_html(
                fetcher(DETALLE_BALOTO_URL.format(sorteo=sorteo)),
                expected_sorteo=sorteo,
                require_prizes=True,
            )
            if not is_prize_table_settled(det_b['prizes']):
                return None
            baloto_part = {'numbers': det_b['numbers'], 'sb': det_b['sb'], 'prizes': det_b['prizes']}
            fecha = det_b['fecha']

        rev_part = existing_cached.get('revancha') if existing_cached else None
        if rev_part is not None and not is_prize_table_settled(rev_part.get('prizes', {})):
            rev_part = None

        if need_revancha and rev_part is None:
            det_r = parse_detail_draw_html(
                fetcher(DETALLE_REVANCHA_URL.format(sorteo=sorteo)),
                expected_sorteo=sorteo,
                require_prizes=True,
            )
            if not is_prize_table_settled(det_r['prizes']):
                return {
                    'sorteo': sorteo,
                    'fecha': fecha,
                    'baloto': baloto_part,
                    'revancha': None,
                }
            rev_part = {'numbers': det_r['numbers'], 'sb': det_r['sb'], 'prizes': det_r['prizes']}

        return {
            'sorteo': sorteo,
            'fecha': fecha,
            'baloto': baloto_part,
            'revancha': rev_part,
        }

    def _save_synced_history(self, merged_rows: list[dict], added: list[int]) -> None:
        if not added and self.history_path.exists():
            return
        try:
            lock_file = self.history_path.parent / f'.{self.history_path.name}.lock'
            with file_lock(lock_file):
                save_history(merged_rows, self.history_path)
        except OSError:
            fallback = self.directory / 'historial_baloto.csv'
            save_history(merged_rows, fallback)
            self.history_path = fallback

    def sync(self, fetcher=fetch_html, clock: datetime | None = None) -> dict:
        with self.sync_mutex:
            ts = clock or now()
            existing_history = load_history(self.history_path)
            merged_rows, added_sorteos, latest_rec = _plan_history_sync(existing_history, fetcher=fetcher)
            latest_sorteo = latest_rec['sorteo']

            with self.mutex:
                self._reload_if_changed()
                cached_snapshot = copy.deepcopy(self.state['official_draws'])
                needed_draws: dict[int, bool] = {}
                for ticket in self.state['tickets'].values():
                    if ticket['status'] == 'cart':
                        continue
                    need_rev = ticket['mode'] == 'baloto_revancha'
                    for d in ticket['draws']:
                        if d['result'] is None and d['sorteo'] <= latest_sorteo:
                            needed_draws[d['sorteo']] = needed_draws.get(d['sorteo'], False) or need_rev

            fetched_draws: dict[str, dict] = {}
            for sorteo, need_rev in sorted(needed_draws.items()):
                key = str(sorteo)
                existing_cached = cached_snapshot.get(key)
                if self._is_draw_ready_for_ticket(existing_cached, need_rev):
                    continue
                try:
                    entry = self._fetch_official_draw_entry(sorteo, need_rev, existing_cached, fetcher)
                except DrawNotFoundError:
                    # Número de sorteo inexistente en baloto.com: no bloquea la sincronización del resto.
                    continue
                if entry is not None:
                    fetched_draws[key] = entry

            # Todas las descargas y validaciones HTML tuvieron éxito: persistimos CSV y estado.
            self._save_synced_history(merged_rows, added_sorteos)

            with self.transaction():
                for key, entry in fetched_draws.items():
                    prev = self.state['official_draws'].get(key)
                    if prev and prev.get('revancha') and not entry.get('revancha'):
                        entry = {**entry, 'revancha': prev['revancha']}
                    self.state['official_draws'][key] = entry

                evaluated_draws = []
                for ticket in self.state['tickets'].values():
                    if ticket['status'] == 'cart':
                        continue
                    need_rev = ticket['mode'] == 'baloto_revancha'
                    for d in ticket['draws']:
                        if d['result'] is not None or d['sorteo'] > latest_sorteo:
                            continue
                        official = self.state['official_draws'].get(str(d['sorteo']))
                        if not self._is_draw_ready_for_ticket(official, need_rev):
                            continue
                        d['result'] = evaluate_ticket_draw(ticket, official, ts.isoformat())
                        evaluated_draws.append({
                            'ticket_id': ticket['id'],
                            'sorteo': d['sorteo'],
                            'prize_cop': d['result']['prize_cop'],
                        })
                    if all(d['result'] is not None for d in ticket['draws']):
                        ticket['status'] = 'complete'

                self.state['last_sync_at'] = ts.isoformat()
                self.save()

            return {
                'latest_sorteo': latest_sorteo,
                'latest_fecha': latest_rec['fecha'],
                'history_added': added_sorteos,
                'evaluated': evaluated_draws,
                'synced_at': ts.isoformat(),
            }


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == 'nt':  # pragma: no cover - Windows fallback
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@contextmanager
def process_lock(directory: Path | str):
    resolved = _ensure_private_dir(Path(directory))
    lock = resolved / '.server.lock'
    if fcntl is not None or msvcrt is not None:
        try:
            with file_lock(lock, non_blocking=True) as fd:
                os.lseek(fd, 0, os.SEEK_SET)
                os.ftruncate(fd, 0)
                os.write(fd, f'{os.getpid()}\n'.encode('ascii'))
                os.fsync(fd)
                try:
                    yield
                finally:
                    lock.unlink(missing_ok=True)
        except (BlockingIOError, PermissionError, OSError) as err:
            raise ValueError(
                f'Otro servidor activo usa {resolved}. '
                'Consulta references/seguimiento-cli.md.'
            ) from err
        return

    # Fallback sin fcntl/msvcrt: verifica si el PID registrado sigue vivo.
    if lock.exists():
        try:
            existing_pid = int(lock.read_text(encoding='utf-8').strip())
        except (OSError, ValueError):
            existing_pid = -1
        if not _pid_is_alive(existing_pid):
            lock.unlink(missing_ok=True)
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as err:
        raise ValueError(
            f'Otro servidor activo usa {resolved}. '
            'Consulta references/seguimiento-cli.md.'
        ) from err
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Servidor HTTP local
# ---------------------------------------------------------------------------

def make_handler(store: Store, token: str, fetcher=fetch_html):
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{12,128}', token):
        raise ValueError('Token de seguridad inválido para el servidor local.')

    scripts_dir = Path(__file__).resolve().parent
    web_path = scripts_dir / 'web.html'
    helper_path = scripts_dir / 'carrito_helper.py'
    page_template = (web_path.read_text(encoding='utf-8') if web_path.exists()
                     else '<!doctype html><html lang="es"><body><div id="app"></div></body></html>')

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            if hasattr(self.request, 'settimeout'):
                self.request.settimeout(10)
            super().setup()

        def log_message(self, *_):
            pass

        def respond(self, code: int, value, content_type: str = 'application/json; charset=utf-8',
                    csp_nonce: str | None = None):
            data = (json.dumps(value, ensure_ascii=False).encode('utf-8')
                    if content_type.startswith('application/json')
                    else value.encode('utf-8'))
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            if csp_nonce:
                csp = (
                    f"default-src 'none'; script-src 'nonce-{csp_nonce}'; "
                    "style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; "
                    "frame-ancestors 'none'; form-action 'self'"
                )
            else:
                csp = "default-src 'none'; frame-ancestors 'none'"
            self.send_header('Content-Security-Policy', csp)
            self.end_headers()
            self.wfile.write(data)

        def host_ok(self) -> bool:
            port = self.server.server_port
            return self.headers.get('Host') in (f'127.0.0.1:{port}', f'localhost:{port}')

        def fetch_site_ok(self) -> bool:
            sfs = (self.headers.get('Sec-Fetch-Site') or '').strip().lower()
            return not sfs or sfs in ('same-origin', 'none')

        def check_mutation_headers(self) -> bool:
            if not self.host_ok() or not secrets.compare_digest(self.headers.get('X-CSRF-Token', ''), token):
                self.respond(403, {'error': 'Solicitud local no autorizada. Recarga el tablero.'})
                return False
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}',
                                         f'http://localhost:{self.server.server_port}'):
                self.respond(403, {'error': 'Origen no autorizado.'})
                return False
            if not self.fetch_site_ok():
                self.respond(403, {'error': 'Contexto Fetch no autorizado.'})
                return False
            return True

        def do_GET(self):
            if not self.host_ok():
                return self.respond(403, {'error': 'Host no autorizado.'})
            parsed = urlsplit(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)

            if path.startswith('/api/') and not self.fetch_site_ok():
                return self.respond(403, {'error': 'Contexto Fetch no autorizado.'})

            if path == '/api/health':
                try:
                    rows = load_history(store.history_path)
                except ValueError as error:
                    return self.respond(500, {'error': str(error)})
                latest = rows[-1]['sorteo'] if rows else None
                return self.respond(200, {
                    'ok': True,
                    'history_draws': len(rows),
                    'latest_sorteo': latest,
                    'next_sorteo': (latest + 1) if latest else 2729,
                    'last_sync_at': store.last_sync_at(),
                    'history_path': str(store.history_path),
                    'carrito_helper_path': str(helper_path),
                })
            if path == '/api/tickets':
                return self.respond(200, store.list())
            match = re.fullmatch(r'/api/tickets/([a-f0-9]{32})', path)
            if match:
                try:
                    return self.respond(200, store.get(match[1]))
                except KeyError:
                    return self.respond(404, {'error': 'Tiquete no encontrado.'})
            if path == '/api/history':
                try:
                    limit = int(query.get('limit', ['50'])[0])
                    integer(limit, 'Límite', 1, 5000)
                    rows = load_history(store.history_path)
                    return self.respond(200, list(reversed(rows[-limit:])))
                except ValueError as error:
                    return self.respond(400, {'error': str(error)})
            if path == '/api/history/stats':
                try:
                    game = query.get('game', ['baloto'])[0]
                    last_raw = query.get('last', [None])[0]
                    last_val = int(last_raw) if last_raw else None
                    rows = load_history(store.history_path)
                    return self.respond(200, compute_stats(rows, game=game, last=last_val))
                except ValueError as error:
                    return self.respond(400, {'error': str(error)})
            if path == '/api/suggest':
                try:
                    count_raw = query.get('count', ['1'])[0]
                    count = integer(int(count_raw), 'Cantidad', 1, 50)
                    strat = query.get('strategy', ['bayesian'])[0]
                    game = query.get('game', ['baloto'])[0]
                    combo = query.get('combo', [None])[0]
                    rows = load_history(store.history_path)
                    res = generate_suggested_lines(rows, count=count, strategy=strat, game=game, combo_label=combo)
                    return self.respond(200, res)
                except ValueError as error:
                    return self.respond(400, {'error': str(error)})
            if path in ('/', '/estadisticas') or re.fullmatch(r'/seguimiento/[a-f0-9]{32}', path):
                csp_nonce = secrets.token_hex(16)
                rendered = (
                    page_template
                    .replace('__CSP_NONCE__', csp_nonce)
                    .replace('__CSRF_TOKEN__', token)
                    .replace('__TOKEN__', token)
                )
                return self.respond(200, rendered, 'text/html; charset=utf-8', csp_nonce=csp_nonce)
            return self.respond(404, {'error': 'Ruta no encontrada.'})

        def do_DELETE(self):
            if not self.check_mutation_headers():
                return
            path = urlsplit(self.path).path
            match = re.fullmatch(r'/api/tickets/([a-f0-9]{32})', path)
            if not match:
                return self.respond(404, {'error': 'Ruta no encontrada.'})
            try:
                store.delete(match[1])
                return self.respond(200, {'deleted': match[1]})
            except KeyError:
                return self.respond(404, {'error': 'Tiquete no encontrado.'})
            except OSError:
                return self.respond(500, {'error': 'No se pudo guardar el estado local.'})

        def do_POST(self):
            if not self.check_mutation_headers():
                return
            try:
                raw_len = self.headers.get('Content-Length')
                length = int(raw_len) if raw_len is not None else 0
                if length < 0:
                    return self.respond(400, {'error': 'Content-Length inválido.'})
                if length > 131072:
                    return self.respond(413, {'error': 'La solicitud supera el límite permitido (128 KB).'})

                path = urlsplit(self.path).path
                if length == 0:
                    if path == '/api/tickets':
                        raise ValueError('Se requiere contenido JSON para registrar un tiquete.')
                    payload: dict = {}
                else:
                    ctype = self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
                    if ctype != 'application/json':
                        return self.respond(415, {'error': 'Se requiere Content-Type: application/json.'})
                    raw_body = self.rfile.read(length)
                    payload = json.loads(raw_body.decode('utf-8'))
                    if not isinstance(payload, dict):
                        raise ValueError('El cuerpo JSON debe ser un objeto.')

                if path == '/api/tickets':
                    return self.respond(201, store.create(payload))
                if path == '/api/sync':
                    return self.respond(200, store.sync(fetcher=fetcher))
                match = re.fullmatch(r'/api/tickets/([a-f0-9]{32})/(confirm|delete)', path)
                if match:
                    key, act = match[1], match[2]
                    if act == 'delete':
                        store.delete(key)
                        return self.respond(200, {'deleted': key})
                    return self.respond(
                        200,
                        store.action(key, act, start_draw=payload.get('start_draw')),
                    )
                return self.respond(404, {'error': 'Ruta no encontrada.'})
            except KeyError:
                return self.respond(404, {'error': 'Tiquete no encontrado.'})
            except (UpstreamSyncError, URLError, TimeoutError, http.client.HTTPException) as error:
                detail = getattr(error, 'reason', None) or str(error) or 'Tiempo de espera agotado.'
                return self.respond(502, {'error': f'Error al sincronizar con baloto.com: {detail}'})
            except (ValueError, TypeError, UnicodeDecodeError) as error:
                return self.respond(400, {'error': str(error)})
            except OSError:
                return self.respond(500, {'error': 'No se pudo guardar el estado local.'})

    return Handler


def _read_json_argument(spec: str) -> dict:
    raw = sys.stdin.read() if spec == '-' else Path(spec).expanduser().read_text(encoding='utf-8')
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError('La configuración de la jugada debe ser un objeto JSON.')
    return parsed


# ---------------------------------------------------------------------------
# Interfaz de línea de comandos
# ---------------------------------------------------------------------------

def main(argv=None) -> None:
    reconfigure_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=default_data_dir(),
                        help='Carpeta privada para tiquetes y caché de premios oficiales.')
    parser.add_argument('--history', type=Path, default=DEFAULT_HISTORY_PATH,
                        help='Ruta al CSV del historial oficial de sorteos.')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--poll-minutes', type=int, default=30,
                        help='Minutos entre consultas automáticas a baloto.com mientras el servidor está activo.')
    parser.add_argument('--prepare', type=str, metavar='ARCHIVO_O_GUION',
                        help="Ruta JSON o '-' (stdin) con una jugada para registrar.")
    parser.add_argument('--no-serve', action='store_true',
                        help='Con --prepare, registra la jugada, imprime JSON y termina sin abrir el servidor.')
    parser.add_argument('--confirm', type=str, metavar='TICKET_ID',
                        help="Confirma como comprado ('active') un tiquete previamente guardado en estado 'cart'.")
    parser.add_argument('--start-draw', type=int,
                        help='Sorteo inicial opcional al confirmar un tiquete con --confirm.')
    parser.add_argument('--sync-history', action='store_true',
                        help='Sincroniza nuevos sorteos publicados en baloto.com hacia el CSV histórico y sale.')
    parser.add_argument('--from-draw', type=int,
                        help='Sorteo inicial opcional al ejecutar --sync-history.')
    parser.add_argument('--check', action='store_true',
                        help='Sincroniza baloto.com, evalúa todos los tiquetes activos y sale (útil para cron).')
    parser.add_argument('--stats', action='store_true',
                        help='Calcula estadísticas sobre el historial oficial y sale.')
    parser.add_argument('--suggest', type=int, nargs='?', const=1, metavar='N',
                        help='Genera N combinaciones sugeridas basadas en el historial y sale (por defecto: 1).')
    parser.add_argument('--strategy', choices=list(SUGGESTION_STRATEGIES), default='bayesian',
                        help='Estrategia combinatoria o estadística para --suggest (por defecto: bayesian).')
    parser.add_argument('--combo', type=str,
                        help='Etiqueta opcional de combo para las líneas generadas por --suggest.')
    parser.add_argument('--game', choices=['baloto', 'revancha', 'both'], default='baloto')
    parser.add_argument('--last', type=int, help='Limita --stats a los últimos N sorteos.')
    parser.add_argument('--json', action='store_true', help='Imprime salida en formato JSON.')
    args = parser.parse_args(argv)

    one_shot_modes = sum([
        bool(args.sync_history),
        bool(args.stats),
        bool(args.suggest is not None),
        bool(args.confirm),
        bool(args.check),
    ])
    if one_shot_modes > 1:
        parser.error('Usa solo un modo por ejecución (--sync-history, --stats, --suggest, --confirm o --check).')
    if args.prepare and one_shot_modes > 0:
        parser.error('No combines --prepare con --sync-history, --stats, --suggest, --confirm ni --check.')
    if args.no_serve and not args.prepare:
        parser.error('--no-serve requiere indicar --prepare.')
    if args.start_draw is not None and not args.confirm:
        parser.error('--start-draw solo aplica junto con --confirm.')

    if args.sync_history:
        summary = sync_history(args.history, from_draw=args.from_draw)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            nuevos = ', '.join(f'#{s}' for s in summary['added']) if summary['added'] else 'ninguno'
            print(f"Historial al día hasta #{summary['latest_sorteo']} ({summary['latest_fecha']}). "
                  f"Nuevos agregados: {nuevos}. Total sorteos: {summary['total_draws']}.")
        return

    if args.stats:
        rows = load_history(args.history)
        stats = compute_stats(rows, game=args.game, last=args.last)
        if args.json:
            print(json.dumps(stats, ensure_ascii=False, indent=2))
        else:
            print(f"Estadísticas ({stats['game']}) · {stats['draw_count']} sorteos "
                  f"(#{stats['from_sorteo']} a #{stats['to_sorteo']}, hasta {stats['to_fecha']})")
            print(f"Chi² balotas (gl=42): {stats['chi2_numbers']['chi2']} · "
                  f"Chi² Superbalota (gl=15): {stats['chi2_superbalota']['chi2']}")
            print(f"Suma media: {stats['sum_stats']['mean']} (IQR: {stats['sum_stats']['q25']}–{stats['sum_stats']['q75']}) · "
                  f"Par/Impar típico: {stats['odd_even']['most_common']} ({stats['odd_even']['percentages'].get(stats['odd_even']['most_common'], 0)}%) · "
                  f"Con ≥1 consecutivo: {stats['consecutive_pairs']['at_least_one_pct']}%")
            hot = ', '.join(f"{x['number']:02d} ({x['count']})" for x in stats['hot_numbers'][:7])
            cold = ', '.join(f"{x['number']:02d} ({x['count']})" for x in stats['cold_numbers'][:7])
            over = ', '.join(f"{x['number']:02d} ({x['draws_since_seen']}s)" for x in stats['overdue_numbers'][:7])
            top_bay = ', '.join(f"{x['number']:02d}" for x in stats['bayesian']['top_numbers'][:5])
            print(f'Más frecuentes: {hot}')
            print(f'Menos frecuentes: {cold}')
            print(f'Mayor rezago: {over}')
            print(f'Top Bayesiano (Beta-Binomial): {top_bay}')
        return

    if args.suggest is not None:
        rows = load_history(args.history)
        res = generate_suggested_lines(
            rows,
            count=args.suggest,
            strategy=args.strategy,
            game=args.game,
            combo_label=args.combo,
        )
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(res['text'])
        return

    if args.confirm:
        store = Store(args.data_dir, history_path=args.history)
        try:
            updated = store.action(args.confirm.strip(), 'confirm', start_draw=args.start_draw)
        except KeyError as err:
            raise ValueError(f'Tiquete no encontrado: {args.confirm}') from err
        if args.json:
            print(json.dumps(updated, ensure_ascii=False, indent=2))
        else:
            print(
                f"Tiquete confirmado: {updated['label']} ({updated['id']}) · "
                f"Estado: {updated['status']} · Sorteos #{updated['start_draw']}→#{updated['end_draw']} · "
                f"Costo: {fmt_cop(updated['cost'])}."
            )
        return

    payload = _read_json_argument(args.prepare) if args.prepare else None
    if payload is not None:
        make_ticket(payload, now(), default_start_draw=_default_next_draw(args.history))

    if args.check:
        store = Store(args.data_dir, history_path=args.history)
        summary = store.sync()
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            total_won = sum(item['prize_cop'] for item in summary['evaluated'])
            print(f"Sincronizado hasta sorteo #{summary['latest_sorteo']} ({summary['latest_fecha']}). "
                  f"Sorteos evaluados: {len(summary['evaluated'])} · Premios nuevos: {fmt_cop(total_won)}.")
        return

    if payload is not None and args.no_serve:
        store = Store(args.data_dir, history_path=args.history)
        created = store.create(payload)
        print(json.dumps(created, ensure_ascii=False, indent=2))
        return

    integer(args.port, 'Puerto', 1024, 65535)
    integer(args.poll_minutes, 'Intervalo de consulta (minutos)', 1, 1440)

    with process_lock(args.data_dir):
        store = Store(args.data_dir, history_path=args.history)
        token = secrets.token_hex(24)
        handler = make_handler(store, token)
        with ThreadingHTTPServer(('127.0.0.1', args.port), handler) as server:
            server.daemon_threads = True
            created = store.create(payload) if payload is not None else None
            stop = threading.Event()

            def safe_sync():
                try:
                    store.sync()
                except Exception as error:
                    print(f'Aviso de red/sincronización: {error}', file=sys.stderr, flush=True)

            def worker():
                safe_sync()
                while not stop.wait(args.poll_minutes * 60):
                    safe_sync()

            thread = threading.Thread(target=worker, daemon=True)
            thread.start()

            address = f'http://127.0.0.1:{args.port}'
            print(f'Baloto — Seguimiento y estadística local\n{address}', flush=True)
            if created:
                print(address + created['url'], flush=True)
            print(f'Datos privados: {store.directory}\nHistorial: {store.history_path}\nCtrl+C para detener.',
                  flush=True)

            def terminate(*_):
                raise KeyboardInterrupt

            signal.signal(signal.SIGTERM, terminate)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                stop.set()
                thread.join(timeout=5)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, URLError, http.client.HTTPException) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
