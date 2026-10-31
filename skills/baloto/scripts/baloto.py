#!/usr/bin/env python3
"""Herramienta estándar de Baloto: historial oficial, estadística, preparación y seguimiento real en COP."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import combinations
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import statistics
import sys
import tempfile
import threading
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
import urllib.request
import uuid

UTC = timezone.utc
RESULTADOS_URL = 'https://baloto.com/resultados'
DETALLE_BALOTO_URL = 'https://baloto.com/resultados-baloto/{sorteo}'
DETALLE_REVANCHA_URL = 'https://baloto.com/resultados-revancha/{sorteo}'
USER_AGENT = 'Mozilla/5.0 (compatible; baloto-skill/1.0)'

DEFAULT_PRICES = {
    'baloto': 6000,
    'baloto_revancha': 9000,
}

# Orden oficial de las 8 categorías publicadas por baloto.com en cada sorteo.
# '0+SB' agrupa tanto 0 aciertos + Superbalota como 1 acierto + Superbalota.
PRIZE_CATEGORY_KEYS = [
    '5+SB', '5', '4+SB', '4', '3+SB', '3', '2+SB', '0+SB',
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

DEFAULT_HISTORY_PATH = Path(__file__).resolve().parents[1] / 'data/historial_baloto.csv'


def now() -> datetime:
    return datetime.now(UTC)


def integer(value, field: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{field}: debe ser un entero entre {low} y {high}.')
    return value


def fmt_cop(amount: int) -> str:
    sign = '−' if amount < 0 else ''
    return f"{sign}${abs(amount):,}".replace(',', '.')


def atomic_write_text(path: Path, content: str) -> None:
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f'.{path.name}-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def fetch_html(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', errors='replace')


# ---------------------------------------------------------------------------
# Validación y manejo del historial oficial (CSV) y estadísticas
# ---------------------------------------------------------------------------

def validate_draw_record(record: dict) -> dict:
    sorteo = integer(record.get('sorteo'), 'Sorteo', 1, 999999)
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


def load_history(path: Path = DEFAULT_HISTORY_PATH) -> list[dict]:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        return []
    rows = []
    with path.open('r', encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        for raw in reader:
            rows.append(validate_draw_record({
                'sorteo': int(raw['sorteo']),
                'fecha': raw['fecha'].strip(),
                'baloto': [int(raw[f'b{i}']) for i in range(1, 6)],
                'sb_baloto': int(raw['sb_baloto']),
                'revancha': [int(raw[f'r{i}']) for i in range(1, 6)],
                'sb_revancha': int(raw['sb_revancha']),
            }))
    rows.sort(key=lambda r: r['sorteo'])
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
    atomic_write_text(path, '\n'.join(lines) + '\n')


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
    num_counts = Counter()
    sb_counts = Counter()
    pair_counts = Counter()
    sums = []

    for nums, sb in samples:
        num_counts.update(nums)
        sb_counts[sb] += 1
        sums.append(sum(nums))
        for a, b in combinations(sorted(nums), 2):
            pair_counts[(a, b)] += 1

    def lag_for(target: int, is_sb: bool = False) -> int:
        idx = 1 if is_sb else 0
        for offset, draw_sets in enumerate(reversed(per_draw_sets)):
            if target in draw_sets[idx]:
                return offset
        return draw_count

    exp_num = m * (5.0 / 43.0)
    sd_num = math.sqrt(m * (5.0 / 43.0) * (38.0 / 43.0))
    numbers = []
    chi2_num = 0.0
    for n in range(1, 44):
        c = num_counts[n]
        chi2_num += ((c - exp_num) ** 2) / exp_num
        z = (c - exp_num) / sd_num if sd_num > 0 else 0.0
        numbers.append({
            'number': n,
            'count': c,
            'expected': round(exp_num, 2),
            'z_score': round(z, 3),
            'draws_since_seen': lag_for(n, is_sb=False),
        })

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
        'sum_stats': {
            'min': min(sums),
            'max': max(sums),
            'mean': round(statistics.mean(sums), 2),
            'median': round(statistics.median(sums), 2),
        },
    }


# ---------------------------------------------------------------------------
# Parsers del HTML oficial de baloto.com y cálculo real de premios COP
# ---------------------------------------------------------------------------

def parse_spanish_date(html: str) -> str:
    m = re.search(r'\b[A-Za-zÁÉÍÓÚáéíóúñÑ]+\s+(\d{1,2})\s+de\s+([A-Za-z]+)\s+de\s+(\d{4})\b', html)
    if m:
        day, month_name, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        month = MESES.get(month_name)
        if month is not None:
            return f'{year:04d}-{month:02d}-{day:02d}'
    m_slash = re.search(r'\b(\d{2})/(\d{2})/(\d{4})\b', html)
    if m_slash:
        d, mo, y = map(int, m_slash.groups())
        return f'{y:04d}-{mo:02d}-{d:02d}'
    raise ValueError('No se pudo leer la fecha oficial del sorteo en baloto.com.')


def parse_latest_results_html(html: str) -> dict:
    m_sorteo = re.search(r'Resultado\s+sorteo\s+#(\d+)', html, re.I)
    balls = re.findall(r'class="(?:yellow-ball|red-ball)[^"]*">\s*(\d+)\s*</div>', html)
    if not m_sorteo or len(balls) < 12:
        raise ValueError('Formato inesperado en baloto.com/resultados.')
    sorteo = int(m_sorteo.group(1))
    fecha = parse_spanish_date(html)
    baloto = sorted(int(x) for x in balls[0:5])
    sb_b = int(balls[5])
    revancha = sorted(int(x) for x in balls[6:11])
    sb_r = int(balls[11])
    return validate_draw_record({
        'sorteo': sorteo,
        'fecha': fecha,
        'baloto': baloto,
        'sb_baloto': sb_b,
        'revancha': revancha,
        'sb_revancha': sb_r,
    })


def parse_prize_table_html(html: str) -> dict[str, int]:
    start = html.find('PREMIO TOTAL')
    end = html.find('next-contest', start) if start != -1 else -1
    if start == -1 or end == -1:
        raise ValueError('No se encontró la tabla oficial de premios por categoría.')
    chunk = html[start:end]
    rows = re.findall(r'<tr class="br-header-left br-header-right">(.*?)</tr>', chunk, re.S)
    if len(rows) != 8:
        raise ValueError(f'Se esperaban 8 categorías oficiales de premio; llegaron {len(rows)}.')
    table: dict[str, int] = {}
    for key, row in zip(PRIZE_CATEGORY_KEYS, rows):
        vals = re.findall(r'dark-blue">\s*([^<]+?)\s*</td>', row)
        if len(vals) != 3:
            raise ValueError('Fila de premio con formato inesperado en baloto.com.')
        prize_per_winner = int(re.sub(r'[^\d]', '', vals[2]) or '0')
        table[key] = prize_per_winner
    return table


def parse_detail_draw_html(html: str, expected_sorteo: int | None = None,
                           require_prizes: bool = True) -> dict:
    m_sorteo = re.search(r'sorteo\s*#?\s*(\d+)', html, re.I)
    sorteo = int(m_sorteo.group(1)) if m_sorteo else expected_sorteo
    if sorteo is None:
        raise ValueError('No se pudo determinar el número de sorteo en el detalle.')
    if expected_sorteo is not None and sorteo != expected_sorteo:
        raise ValueError(f'El detalle devolvió sorteo #{sorteo} en lugar de #{expected_sorteo}.')
    balls = re.findall(r'class="(?:yellow-ball|red-ball)[^"]*">\s*(\d+)\s*</div>', html)
    if len(balls) < 6:
        raise ValueError(f'No se encontraron las 6 balotas del sorteo #{sorteo}.')
    nums = sorted(int(x) for x in balls[0:5])
    if len(set(nums)) != 5 or not all(1 <= n <= 43 for n in nums):
        raise ValueError(f'Balotas inválidas en el detalle del sorteo #{sorteo}.')
    sb = integer(int(balls[5]), f'Superbalota #{sorteo}', 1, 16)
    fecha = parse_spanish_date(html)
    prizes = parse_prize_table_html(html) if require_prizes else {}
    return {'sorteo': sorteo, 'fecha': fecha, 'numbers': nums, 'sb': sb, 'prizes': prizes}


def sync_history(path: Path = DEFAULT_HISTORY_PATH, fetcher=fetch_html,
                 from_draw: int | None = None) -> dict:
    existing = load_history(path)
    by_sorteo = {r['sorteo']: r for r in existing}
    latest = parse_latest_results_html(fetcher(RESULTADOS_URL))

    if from_draw is not None:
        start = integer(from_draw, 'Sorteo inicial', 1, latest['sorteo'])
    elif by_sorteo:
        start = max(by_sorteo) + 1
    else:
        start = latest['sorteo']

    added = []
    for s in range(start, latest['sorteo'] + 1):
        if s in by_sorteo:
            continue
        if s == latest['sorteo']:
            by_sorteo[s] = latest
        else:
            det_b = parse_detail_draw_html(
                fetcher(DETALLE_BALOTO_URL.format(sorteo=s)), expected_sorteo=s, require_prizes=False
            )
            det_r = parse_detail_draw_html(
                fetcher(DETALLE_REVANCHA_URL.format(sorteo=s)), expected_sorteo=s, require_prizes=False
            )
            by_sorteo[s] = validate_draw_record({
                'sorteo': s,
                'fecha': det_b['fecha'],
                'baloto': det_b['numbers'],
                'sb_baloto': det_b['sb'],
                'revancha': det_r['numbers'],
                'sb_revancha': det_r['sb'],
            })
        added.append(s)

    if added or not Path(path).exists():
        save_history(list(by_sorteo.values()), path)

    return {
        'latest_sorteo': latest['sorteo'],
        'latest_fecha': latest['fecha'],
        'added': added,
        'total_draws': len(by_sorteo),
    }


def prize_for_hits(prize_table: dict[str, int], hits: int, sb_match: bool) -> tuple[int, str | None]:
    if hits >= 3:
        key = f'{hits}+SB' if sb_match else f'{hits}'
        return int(prize_table.get(key, 0)), key
    if sb_match:
        key = '2+SB' if hits == 2 else '0+SB'
        return int(prize_table.get(key, 0)), key
    return 0, None


# ---------------------------------------------------------------------------
# Validación de jugadas / tiquetes y persistencia local
# ---------------------------------------------------------------------------

def parse_lines(value: str) -> list[dict]:
    if not isinstance(value, str) or len(value) > 40000:
        raise ValueError('Introduce las líneas como texto (máximo 40.000 caracteres).')
    lines, seen = [], set()
    for index, raw in enumerate(value.strip().splitlines(), 1):
        clean = raw.strip()
        if not clean or clean.startswith('#'):
            continue
        combo = None
        if ':' in clean:
            prefix, rest = clean.split(':', 1)
            combo = prefix.strip()
            if not combo or len(combo) > 24:
                raise ValueError(f'Línea {index}: etiqueta de combo inválida.')
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
        entry = {'numbers': numbers, 'extra': extra}
        if combo:
            entry['combo'] = combo
        lines.append(entry)
    if not 1 <= len(lines) <= 500:
        raise ValueError('Introduce entre 1 y 500 líneas por tiquete.')
    return lines


def make_ticket(payload: dict, clock: datetime, default_start_draw: int = 2729) -> dict:
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
    start_draw = (default_start_draw if raw_start is None
                  else integer(raw_start, 'Sorteo inicial', 1, 999999))
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

    result = {
        'fecha': official['fecha'],
        'baloto': {'numbers': official['baloto']['numbers'], 'extra': baloto_sb},
        'revancha': ({'numbers': official['revancha']['numbers'], 'extra': rev_sb}
                     if has_revancha and official.get('revancha') else None),
        'lines': line_results,
        'prize_cop': total_prize,
        'checked_at': checked_at,
    }
    return result


def public_ticket(ticket: dict) -> dict:
    out = json.loads(json.dumps(ticket))
    won = sum(d['result']['prize_cop'] for d in out['draws'] if d['result'] is not None)
    played_draws = sum(1 for d in out['draws'] if d['result'] is not None)
    spent = 0 if out['status'] == 'cart' else out['cost']
    out.update(
        won=won,
        spent=spent,
        balance=won - spent,
        played_draws=played_draws,
        pending_draws=len(out['draws']) - played_draws,
        end_draw=out['start_draw'] + out['draw_count'] - 1,
        url=f"/seguimiento/{out['id']}",
    )
    return out


class Store:
    def __init__(self, directory: Path | str, history_path: Path = DEFAULT_HISTORY_PATH):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'state.json'
        self.history_path = Path(history_path).expanduser().resolve()
        self.mutex = threading.RLock()
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding='utf-8'))
        else:
            self.state = {'version': 2, 'tickets': {}, 'official_draws': {}, 'last_sync_at': None}
        if (self.state.get('version') != 2
                or not isinstance(self.state.get('tickets'), dict)
                or not isinstance(self.state.get('official_draws'), dict)):
            raise ValueError('Formato de estado local no compatible; respalda state.json antes de migrar.')

    def save(self) -> None:
        atomic_write_text(self.path, json.dumps(self.state, ensure_ascii=False, indent=2))

    @contextmanager
    def transaction(self):
        with self.mutex:
            before = json.loads(json.dumps(self.state))
            try:
                yield
            except Exception:
                self.state = before
                raise

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

    def action(self, key: str, action: str, clock: datetime | None = None) -> dict:
        with self.transaction():
            ts = clock or now()
            ticket = self.state['tickets'][key]
            if action == 'confirm':
                if ticket['status'] == 'cart':
                    ticket['status'] = 'active'
                    ticket['confirmed_at'] = ts.isoformat()
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
            return public_ticket(self.state['tickets'][key])

    def list(self) -> list[dict]:
        with self.mutex:
            items = []
            for t in reversed(list(self.state['tickets'].values())):
                pub = public_ticket(t)
                items.append({
                    'id': pub['id'],
                    'label': pub['label'],
                    'mode': pub['mode'],
                    'status': pub['status'],
                    'start_draw': pub['start_draw'],
                    'end_draw': pub['end_draw'],
                    'cost': pub['cost'],
                    'won': pub['won'],
                    'balance': pub['balance'],
                    'played_draws': pub['played_draws'],
                    'draw_count': pub['draw_count'],
                    'url': pub['url'],
                })
            return items

    def _evaluate_cached(self, ticket: dict, ts: datetime) -> bool:
        if ticket['status'] == 'cart':
            return False
        changed = False
        for d in ticket['draws']:
            if d['result'] is not None:
                continue
            cached = self.state['official_draws'].get(str(d['sorteo']))
            if not cached:
                continue
            if ticket['mode'] == 'baloto_revancha' and not cached.get('revancha'):
                continue
            d['result'] = evaluate_ticket_draw(ticket, cached, ts.isoformat())
            changed = True
        if all(d['result'] is not None for d in ticket['draws']):
            ticket['status'] = 'complete'
        return changed

    def _ensure_official_draw(self, sorteo: int, need_revancha: bool, fetcher) -> dict:
        key = str(sorteo)
        cached = self.state['official_draws'].get(key)
        if cached and (not need_revancha or cached.get('revancha')):
            return cached

        det_b = parse_detail_draw_html(
            fetcher(DETALLE_BALOTO_URL.format(sorteo=sorteo)),
            expected_sorteo=sorteo,
            require_prizes=True,
        )
        rev_data = cached.get('revancha') if cached else None
        if need_revancha and not rev_data:
            det_r = parse_detail_draw_html(
                fetcher(DETALLE_REVANCHA_URL.format(sorteo=sorteo)),
                expected_sorteo=sorteo,
                require_prizes=True,
            )
            rev_data = {'numbers': det_r['numbers'], 'sb': det_r['sb'], 'prizes': det_r['prizes']}

        entry = {
            'sorteo': sorteo,
            'fecha': det_b['fecha'],
            'baloto': {'numbers': det_b['numbers'], 'sb': det_b['sb'], 'prizes': det_b['prizes']},
            'revancha': rev_data,
        }
        self.state['official_draws'][key] = entry
        return entry

    def sync(self, fetcher=fetch_html, clock: datetime | None = None) -> dict:
        ts = clock or now()
        hist_summary = sync_history(self.history_path, fetcher=fetcher)
        latest_sorteo = hist_summary['latest_sorteo']

        with self.transaction():
            evaluated_draws = []
            for ticket in self.state['tickets'].values():
                if ticket['status'] == 'cart':
                    continue
                need_rev = ticket['mode'] == 'baloto_revancha'
                for d in ticket['draws']:
                    if d['result'] is not None or d['sorteo'] > latest_sorteo:
                        continue
                    official = self._ensure_official_draw(d['sorteo'], need_rev, fetcher)
                    d['result'] = evaluate_ticket_draw(ticket, official, ts.isoformat())
                    evaluated_draws.append({'ticket_id': ticket['id'], 'sorteo': d['sorteo'],
                                            'prize_cop': d['result']['prize_cop']})
                if all(d['result'] is not None for d in ticket['draws']):
                    ticket['status'] = 'complete'
            self.state['last_sync_at'] = ts.isoformat()
            self.save()

        return {
            'latest_sorteo': latest_sorteo,
            'latest_fecha': hist_summary['latest_fecha'],
            'history_added': hist_summary['added'],
            'evaluated': evaluated_draws,
            'synced_at': ts.isoformat(),
        }


@contextmanager
def process_lock(directory: Path | str):
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = directory / '.server.lock'
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError(
            f'Otro servidor o un bloqueo previo usa {directory}. '
            'Consulta references/seguimiento-cli.md.'
        )
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
    web_path = Path(__file__).resolve().parent / 'web.html'
    page_template = (web_path.read_text(encoding='utf-8') if web_path.exists()
                     else '<!doctype html><html lang="es"><body><div id="app"></div></body></html>')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, code: int, value, content_type: str = 'application/json; charset=utf-8'):
            data = (json.dumps(value, ensure_ascii=False).encode('utf-8')
                    if content_type.startswith('application/json')
                    else value.encode('utf-8'))
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header(
                'Content-Security-Policy',
                f"default-src 'none'; script-src 'nonce-{token}'; "
                "style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; "
                "frame-ancestors 'none'; form-action 'self'",
            )
            self.end_headers()
            self.wfile.write(data)

        def host_ok(self) -> bool:
            port = self.server.server_port
            return self.headers.get('Host') in (f'127.0.0.1:{port}', f'localhost:{port}')

        def do_GET(self):
            if not self.host_ok():
                return self.respond(403, {'error': 'Host no autorizado.'})
            parsed = urlsplit(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)

            if path == '/api/health':
                rows = load_history(store.history_path)
                latest = rows[-1]['sorteo'] if rows else None
                return self.respond(200, {
                    'ok': True,
                    'history_draws': len(rows),
                    'latest_sorteo': latest,
                    'next_sorteo': (latest + 1) if latest else 2729,
                    'last_sync_at': store.state.get('last_sync_at'),
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
            if path in ('/', '/estadisticas') or re.fullmatch(r'/seguimiento/[a-f0-9]{32}', path):
                return self.respond(200, page_template.replace('__TOKEN__', token), 'text/html; charset=utf-8')
            return self.respond(404, {'error': 'Ruta no encontrada.'})

        def do_POST(self):
            if not self.host_ok() or not secrets.compare_digest(self.headers.get('X-CSRF-Token', ''), token):
                return self.respond(403, {'error': 'Solicitud local no autorizada. Recarga el tablero.'})
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}',
                                         f'http://localhost:{self.server.server_port}'):
                return self.respond(403, {'error': 'Origen no autorizado.'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 1 <= length <= 131072:
                    raise ValueError('Tamaño de solicitud inválido.')
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('Se requiere contenido JSON.')
                payload = json.loads(self.rfile.read(length))
                path = urlsplit(self.path).path

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
                    return self.respond(200, store.action(key, act))
                return self.respond(404, {'error': 'Ruta no encontrada.'})
            except (ValueError, TypeError, UnicodeDecodeError) as error:
                return self.respond(400, {'error': str(error)})
            except KeyError:
                return self.respond(404, {'error': 'Tiquete no encontrado.'})
            except URLError as error:
                return self.respond(502, {'error': f'No se pudo conectar con baloto.com: {error.reason}'})
            except OSError:
                return self.respond(500, {'error': 'No se pudo guardar el estado local.'})

    return Handler


# ---------------------------------------------------------------------------
# Interfaz de línea de comandos
# ---------------------------------------------------------------------------

def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path.home() / '.local/share/baloto',
                        help='Carpeta privada para tiquetes y caché de premios oficiales.')
    parser.add_argument('--history', type=Path, default=DEFAULT_HISTORY_PATH,
                        help='Ruta al CSV del historial oficial de sorteos.')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--poll-minutes', type=int, default=30,
                        help='Minutos entre consultas automáticas a baloto.com mientras el servidor está activo.')
    parser.add_argument('--prepare', type=Path, help='JSON con una jugada para registrar.')
    parser.add_argument('--no-serve', action='store_true',
                        help='Con --prepare, registra la jugada, imprime JSON y termina sin abrir el servidor.')
    parser.add_argument('--sync-history', action='store_true',
                        help='Sincroniza nuevos sorteos publicados en baloto.com hacia el CSV histórico y sale.')
    parser.add_argument('--from-draw', type=int,
                        help='Sorteo inicial opcional al ejecutar --sync-history.')
    parser.add_argument('--check', action='store_true',
                        help='Sincroniza baloto.com, evalúa todos los tiquetes activos y sale (útil para cron).')
    parser.add_argument('--stats', action='store_true',
                        help='Calcula estadísticas sobre el historial oficial y sale.')
    parser.add_argument('--game', choices=['baloto', 'revancha', 'both'], default='baloto')
    parser.add_argument('--last', type=int, help='Limita --stats a los últimos N sorteos.')
    parser.add_argument('--json', action='store_true', help='Imprime salida en formato JSON.')
    args = parser.parse_args(argv)

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
            hot = ', '.join(f"{x['number']:02d} ({x['count']})" for x in stats['hot_numbers'][:7])
            cold = ', '.join(f"{x['number']:02d} ({x['count']})" for x in stats['cold_numbers'][:7])
            over = ', '.join(f"{x['number']:02d} ({x['draws_since_seen']}s)" for x in stats['overdue_numbers'][:7])
            print(f'Más frecuentes: {hot}')
            print(f'Menos frecuentes: {cold}')
            print(f'Mayor rezago: {over}')
        return

    integer(args.port, 'Puerto', 1024, 65535)
    integer(args.poll_minutes, 'Intervalo de consulta (minutos)', 1, 1440)
    payload = json.loads(args.prepare.read_text(encoding='utf-8')) if args.prepare else None
    if payload is not None:
        make_ticket(payload, now())

    with process_lock(args.data_dir):
        store = Store(args.data_dir, history_path=args.history)
        if args.check:
            summary = store.sync()
            if args.json:
                print(json.dumps(summary, ensure_ascii=False, indent=2))
            else:
                total_won = sum(item['prize_cop'] for item in summary['evaluated'])
                print(f"Sincronizado hasta sorteo #{summary['latest_sorteo']} ({summary['latest_fecha']}). "
                      f"Sorteos evaluados: {len(summary['evaluated'])} · Premios nuevos: {fmt_cop(total_won)}.")
            return

        if payload is not None and args.no_serve:
            created = store.create(payload)
            print(json.dumps(created, ensure_ascii=False, indent=2))
            return

        token = secrets.token_hex(24)
        with ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(store, token)) as server:
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
    except (ValueError, OSError, URLError) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
