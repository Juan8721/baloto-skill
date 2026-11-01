from __future__ import annotations

from datetime import datetime, timezone
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, rel_path: str):
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f'No se pudo cargar {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fmt_sorteo_dot(sorteo: int) -> str:
    return f'{sorteo:,}'.replace(',', '.')


def make_resultados_html(sorteo=2729, weekday_txt='Sábado', date_only_txt='31 de Octubre de 2026',
                         baloto=(1, 2, 3, 4, 5), sb_b=6,
                         revancha=(7, 8, 9, 10, 11), sb_r=12):
    balls_html = []
    for n in baloto:
        balls_html.append(f'<div class="yellow-ball">{n:02d}</div>')
    balls_html.append(f'<div class="red-ball">{sb_b:02d}</div>')
    for n in revancha:
        balls_html.append(f'<div class="yellow-ball">{n:02d}</div>')
    balls_html.append(f'<div class="red-ball">{sb_r:02d}</div>')
    return f"""
    <html><body>
      <h1>Resultado sorteo #{sorteo}</h1>
      <div><strong>{weekday_txt}</strong> {date_only_txt}</div>
      {''.join(balls_html)}
    </body></html>
    """


def make_detalle_html(sorteo=2729, weekday_txt='Sábado', date_only_txt='31 de Octubre de 2026',
                      nums=(1, 2, 3, 4, 5), sb=6, prizes=None, winners=None):
    """Replica fielmente el DOM real de baloto.com/resultados-baloto/<sorteo>."""
    default_prizes = [
        4_000_000_000,  # 5+SB
        85_000_000,     # 5
        2_500_000,      # 4+SB
        150_000,        # 4
        55_000,         # 3+SB
        12_000,         # 3
        10_500,         # 2+SB
        6_000,          # 0 o 1 + SB
    ]
    default_winners = [1, 2, 14, 180, 790, 6200, 5100, 23039]
    values = prizes if prizes is not None else default_prizes
    win_counts = winners if winners is not None else default_winners

    balls_html = [f'<div class="yellow-ball">{n:02d}</div>' for n in nums]
    balls_html.append(f'<div class="red-ball">{sb:02d}</div>')

    rows_html = []
    for val, w_cnt in zip(values, win_counts):
        total_pool = val * max(1, w_cnt)
        fmt_total = f'${total_pool:,}'.replace(',', '.')
        fmt_winners = f'{w_cnt:,}'.replace(',', '.')
        fmt_unit = f'${val:,}'.replace(',', '.')
        rows_html.append(
            '<tr class="br-header-left br-header-right">'
            f'<td class="dark-blue">{fmt_total}</td>'
            f'<td class="dark-blue">{fmt_winners}</td>'
            f'<td class="br-header-right dark-blue">{fmt_unit}</td>'
            '</tr>'
        )

    return f"""
    <html><body>
      <aside class="promo-banner">Último acumulado SORTEO 2.600</aside>
      <div id="balotoBgNew" class="container">
        <div class="gotham-medium dark-blue fs-5"><strong>SORTEO {_fmt_sorteo_dot(sorteo)}</strong></div>
        <div><strong>{weekday_txt}</strong></div>
        <div class="gotham-medium dark-blue">{date_only_txt}</div>
        {''.join(balls_html)}
      </div>
      <table>
        <thead><tr><th>CATEGORÍA</th><th>PREMIO TOTAL</th><th>GANADORES</th><th>PREMIO POR GANADOR</th></tr></thead>
        <tbody>{''.join(rows_html)}</tbody>
      </table>
    </body></html>
    """


class HistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baloto = load_module('baloto', 'skills/baloto/scripts/baloto.py')

    def test_bundled_history_integrity_lf_and_draw_2681_fix(self):
        csv_path = ROOT / 'skills/baloto/data/historial_baloto.csv'
        raw_bytes = csv_path.read_bytes()
        self.assertNotIn(b'\r\n', raw_bytes, 'historial_baloto.csv debe usar finales de línea LF')

        rows = self.baloto.load_history(csv_path)
        self.assertGreaterEqual(len(rows), 625)
        self.assertEqual(rows[0]['sorteo'], 2104)
        self.assertEqual(rows[0]['fecha'], '2021-07-21')
        sorteos = [r['sorteo'] for r in rows]
        # baloto.com no publica el #2685 (festivo 20-jul-2026): es el único número omitido.
        expected = [s for s in range(rows[0]['sorteo'], rows[-1]['sorteo'] + 1) if s != 2685]
        self.assertEqual(sorteos, expected)

        by_sorteo = {r['sorteo']: r for r in rows}
        self.assertEqual(by_sorteo[2681]['baloto'], [3, 22, 28, 32, 34])
        self.assertEqual(by_sorteo[2681]['sb_baloto'], 14)
        self.assertEqual(by_sorteo[2681]['revancha'], [29, 35, 38, 41, 42])
        self.assertEqual(by_sorteo[2681]['sb_revancha'], 2)
        self.assertNotEqual(by_sorteo[2681]['revancha'], by_sorteo[2678]['revancha'])
        # Correcciones verificadas contra las páginas oficiales de detalle de baloto.com
        self.assertEqual(by_sorteo[2507]['baloto'], [2, 8, 16, 28, 31])
        self.assertEqual(by_sorteo[2610]['baloto'], [20, 26, 31, 38, 42])
        self.assertEqual(by_sorteo[2610]['revancha'], [15, 16, 20, 29, 39])
        self.assertEqual(by_sorteo[2623]['sb_baloto'], 14)
        self.assertEqual(by_sorteo[2643]['baloto'], [4, 11, 12, 21, 26])
        self.assertEqual((by_sorteo[2667]['baloto'], by_sorteo[2667]['sb_baloto']), ([6, 8, 9, 32, 40], 13))

        prev_fecha = ''
        for row in rows:
            self.assertGreater(row['fecha'], prev_fecha)
            prev_fecha = row['fecha']
            self.assertEqual(len(set(row['baloto'])), 5)
            self.assertEqual(row['baloto'], sorted(row['baloto']))
            self.assertTrue(all(1 <= n <= 43 for n in row['baloto']))
            self.assertTrue(1 <= row['sb_baloto'] <= 16)
            self.assertEqual(len(set(row['revancha'])), 5)
            self.assertEqual(row['revancha'], sorted(row['revancha']))
            self.assertTrue(all(1 <= n <= 43 for n in row['revancha']))
            self.assertTrue(1 <= row['sb_revancha'] <= 16)

    def test_load_history_utf8_bom_cache_and_duplicate_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'bom.csv'
            header = ','.join(self.baloto.HISTORY_COLUMNS)
            row1 = '2728,2026-10-28,11,23,29,36,43,16,17,30,33,34,37,12'
            p.write_bytes(('\ufeff' + header + '\n' + row1 + '\n\n').encode('utf-8'))

            loaded = self.baloto.load_history(p)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]['sorteo'], 2728)

            # Mutating returned list does not corrupt in-memory cache
            loaded[0]['baloto'][0] = 99
            reloaded = self.baloto.load_history(p)
            self.assertEqual(reloaded[0]['baloto'][0], 11)

            # Duplicate sorteo in CSV must raise ValueError
            p.write_text(f'{header}\n{row1}\n{row1}\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                self.baloto.load_history(p)

    def test_compute_stats_chi2_without_replacement_revancha_and_window(self):
        sample = [
            {'sorteo': 2701, 'fecha': '2026-08-26', 'baloto': [1, 2, 3, 4, 5], 'sb_baloto': 6,
             'revancha': [10, 11, 12, 13, 14], 'sb_revancha': 1},
            {'sorteo': 2702, 'fecha': '2026-08-29', 'baloto': [1, 2, 6, 7, 8], 'sb_baloto': 6,
             'revancha': [10, 20, 21, 22, 23], 'sb_revancha': 2},
            {'sorteo': 2703, 'fecha': '2026-08-31', 'baloto': [1, 9, 10, 11, 12], 'sb_baloto': 16,
             'revancha': [30, 31, 32, 33, 34], 'sb_revancha': 16},
        ]
        stats = self.baloto.compute_stats(sample, game='baloto')
        self.assertEqual(stats['draw_count'], 3)
        self.assertEqual(stats['next_sorteo'], 2704)
        by_num = {item['number']: item for item in stats['numbers']}
        self.assertEqual(by_num[1]['count'], 3)
        self.assertEqual(by_num[1]['draws_since_seen'], 0)
        self.assertEqual(by_num[2]['count'], 2)
        self.assertEqual(by_num[2]['draws_since_seen'], 1)
        self.assertEqual(by_num[5]['draws_since_seen'], 2)
        self.assertEqual(by_num[43]['count'], 0)
        self.assertEqual(by_num[43]['draws_since_seen'], 3)

        # Exact finite-population (42/38) chi2 check:
        exp = 3 * (5.0 / 43.0)
        counts = [by_num[n]['count'] for n in range(1, 44)]
        expected_chi2 = round(sum(((c - exp) ** 2) / exp for c in counts) * (42.0 / 38.0), 3)
        self.assertEqual(stats['chi2_numbers']['chi2'], expected_chi2)
        self.assertEqual(stats['chi2_numbers']['df'], 42)
        self.assertEqual(stats['chi2_superbalota']['df'], 15)
        self.assertEqual(stats['top_pairs'][0]['pair'], [1, 2])
        self.assertEqual(stats['top_pairs'][0]['count'], 2)

        rev_stats = self.baloto.compute_stats(sample, game='revancha')
        rev_by_num = {item['number']: item for item in rev_stats['numbers']}
        self.assertEqual(rev_by_num[10]['count'], 2)
        self.assertEqual(rev_by_num[1]['count'], 0)

        win_stats = self.baloto.compute_stats(sample, game='baloto', last=2)
        self.assertEqual(win_stats['draw_count'], 2)
        self.assertEqual(win_stats['from_sorteo'], 2702)

        both_stats = self.baloto.compute_stats(sample, game='both')
        self.assertEqual(both_stats['sample_draws'], 6)

    def test_parsers_handle_live_baloto_dom_entities_and_reject_bad_dates(self):
        det = self.baloto.parse_detail_draw_html(
            make_detalle_html(
                sorteo=2728,
                weekday_txt='Mi&eacute;rcoles',
                date_only_txt='28 de Octubre de 2026',
                nums=(11, 23, 29, 36, 43),
                sb=16,
            ),
            expected_sorteo=2728,
            require_prizes=True,
        )
        self.assertEqual(det['sorteo'], 2728)
        self.assertEqual(det['fecha'], '2026-10-28')
        self.assertEqual(det['numbers'], [11, 23, 29, 36, 43])
        self.assertEqual(det['sb'], 16)
        # Must extract PREMIO POR GANADOR (vals[2] = 6_000), NOT PREMIO TOTAL (23_039 * 6_000)!
        self.assertEqual(det['prizes']['0 o 1 + SB'], 6_000)
        self.assertEqual(det['prizes']['5'], 85_000_000)

        self.assertEqual(self.baloto.parse_spanish_date('<span>28/10/2026</span>'), '2026-10-28')
        for bad_date_html in ('<p>31 de Febrero de 2026</p>', '<p>31/02/2026</p>'):
            with self.assertRaises(ValueError):
                self.baloto.parse_spanish_date(bad_date_html)

    def test_sync_history_appends_and_from_draw_refreshes_existing_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / 'historial_baloto.csv'
            self.baloto.save_history([
                {'sorteo': 2728, 'fecha': '2026-10-28',
                 'baloto': [1, 2, 3, 4, 5], 'sb_baloto': 1,
                 'revancha': [6, 7, 8, 9, 10], 'sb_revancha': 2}
            ], csv_path)

            pages = {
                'https://baloto.com/resultados': make_resultados_html(
                    sorteo=2730, weekday_txt='Lunes', date_only_txt='2 de Noviembre de 2026',
                    baloto=(3, 14, 21, 35, 42), sb_b=9,
                    revancha=(5, 12, 19, 28, 40), sb_r=4,
                ),
                'https://baloto.com/resultados-baloto/2728': make_detalle_html(
                    sorteo=2728, weekday_txt='Miércoles', date_only_txt='28 de Octubre de 2026',
                    nums=(11, 23, 29, 36, 43), sb=16,
                ),
                'https://baloto.com/resultados-revancha/2728': make_detalle_html(
                    sorteo=2728, weekday_txt='Miércoles', date_only_txt='28 de Octubre de 2026',
                    nums=(17, 30, 33, 34, 37), sb=12,
                ),
                'https://baloto.com/resultados-baloto/2729': make_detalle_html(
                    sorteo=2729, weekday_txt='Sábado', date_only_txt='31 de Octubre de 2026',
                    nums=(1, 2, 3, 4, 5), sb=6,
                ),
                'https://baloto.com/resultados-revancha/2729': make_detalle_html(
                    sorteo=2729, weekday_txt='Sábado', date_only_txt='31 de Octubre de 2026',
                    nums=(7, 8, 9, 10, 11), sb=12,
                ),
            }
            summary = self.baloto.sync_history(csv_path, fetcher=pages.__getitem__)
            self.assertEqual(summary['added'], [2729, 2730])

            # Without --from-draw, existing #2728 is untouched; with --from-draw=2728, #2728 is corrected
            refreshed = self.baloto.sync_history(csv_path, fetcher=pages.__getitem__, from_draw=2728)
            self.assertEqual(refreshed['added'], [2728])
            rows = self.baloto.load_history(csv_path)
            self.assertEqual(rows[0]['baloto'], [11, 23, 29, 36, 43])

            # Excessive automatic jump (> MAX_AUTO_SYNC_SPAN) raises UpstreamSyncError
            huge_pages = {
                'https://baloto.com/resultados': make_resultados_html(sorteo=3500),
            }
            with self.assertRaises(self.baloto.UpstreamSyncError):
                self.baloto.sync_history(csv_path, fetcher=huge_pages.__getitem__)

    def test_sync_history_skips_nonexistent_official_draw_number(self):
        # baloto.com redirige un número inexistente (p. ej. #2685) al último sorteo publicado.
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / 'historial_baloto.csv'
            self.baloto.save_history([
                {'sorteo': 2684, 'fecha': '2026-07-18',
                 'baloto': [8, 17, 19, 24, 26], 'sb_baloto': 9,
                 'revancha': [5, 6, 31, 32, 34], 'sb_revancha': 8}
            ], csv_path)
            latest_detail = make_detalle_html(
                sorteo=2686, weekday_txt='Miércoles', date_only_txt='22 de Julio de 2026',
                nums=(3, 6, 19, 30, 33), sb=9,
            )
            pages = {
                'https://baloto.com/resultados': make_resultados_html(
                    sorteo=2686, weekday_txt='Miércoles', date_only_txt='22 de Julio de 2026',
                    baloto=(3, 6, 19, 30, 33), sb_b=9, revancha=(3, 6, 18, 32, 33), sb_r=7,
                ),
                'https://baloto.com/resultados-baloto/2685': latest_detail,
                'https://baloto.com/resultados-revancha/2685': latest_detail,
            }
            summary = self.baloto.sync_history(csv_path, fetcher=pages.__getitem__)
            self.assertEqual(summary['added'], [2686])
            self.assertEqual([r['sorteo'] for r in self.baloto.load_history(csv_path)], [2684, 2686])

    def test_fetch_html_restricts_scheme_hosts_redirects_and_size(self):
        with self.assertRaises(self.baloto.UpstreamSyncError):
            self.baloto.fetch_html('http://baloto.com/resultados')
        with self.assertRaises(self.baloto.UpstreamSyncError):
            self.baloto.fetch_html('https://evil.example/resultados')

        handler = self.baloto._SafeBalotoRedirectHandler()
        req = self.baloto.urllib.request.Request('https://baloto.com/resultados')
        with self.assertRaises(self.baloto.UpstreamSyncError):
            handler.redirect_request(req, None, 302, 'Found', {}, 'https://evil.example/phish')

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def geturl(self):
                return 'https://baloto.com/resultados'

            def read(self, n):
                return b'X' * n

        with patch.object(self.baloto.urllib.request, 'build_opener') as mock_opener:
            mock_opener.return_value.open.return_value = _FakeResp()
            with self.assertRaises(self.baloto.UpstreamSyncError):
                self.baloto.fetch_html('https://baloto.com/resultados', max_bytes=128)


class TicketEngineTests(unittest.TestCase):
    def setUp(self):
        self.baloto = load_module('baloto', 'skills/baloto/scripts/baloto.py')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.history_path = Path(self.temp.name) / 'historial.csv'
        self.baloto.save_history([
            {'sorteo': 2728, 'fecha': '2026-10-28',
             'baloto': [11, 23, 29, 36, 43], 'sb_baloto': 16,
             'revancha': [17, 30, 33, 34, 37], 'sb_revancha': 12}
        ], self.history_path)
        self.store = self.baloto.Store(self.temp.name, history_path=self.history_path)
        self.clock = datetime(2026, 10, 31, 18, 0, tzinfo=timezone.utc)

    def test_exhaustive_12_tier_prize_matrix_and_dual_win(self):
        table = {
            '5+SB': 4_000_000_000,
            '5': 85_000_000,
            '4+SB': 2_500_000,
            '4': 150_000,
            '3+SB': 55_000,
            '3': 12_000,
            '2+SB': 10_500,
            '0 o 1 + SB': 6_000,
        }
        expected = {
            (5, True): (4_000_000_000, '5+SB'),
            (5, False): (85_000_000, '5'),
            (4, True): (2_500_000, '4+SB'),
            (4, False): (150_000, '4'),
            (3, True): (55_000, '3+SB'),
            (3, False): (12_000, '3'),
            (2, True): (10_500, '2+SB'),
            (2, False): (0, None),
            (1, True): (6_000, '0 o 1 + SB'),
            (1, False): (0, None),
            (0, True): (6_000, '0 o 1 + SB'),
            (0, False): (0, None),
        }
        for (hits, sb), want in expected.items():
            with self.subTest(hits=hits, sb=sb):
                self.assertEqual(self.baloto.prize_for_hits(table, hits, sb), want)

        # Same single line winning BOTH Baloto (3+SB = $55.000) and Revancha (3 sin SB = $7.500)
        t = self.store.create({
            'label': 'Doble premio en una línea',
            'mode': 'baloto_revancha',
            'start_draw': 2729,
            'draw_count': 1,
            'already_purchased': True,
            'lines': '01 02 03 07 08 | 06',
        }, self.clock)
        official = {
            'sorteo': 2729,
            'fecha': '2026-10-31',
            'baloto': {'numbers': [1, 2, 3, 20, 21], 'sb': 6, 'prizes': table},
            'revancha': {'numbers': [1, 7, 8, 30, 31], 'sb': 12, 'prizes': {**table, '3': 7_500, '0 o 1 + SB': 3_000}},
        }
        res = self.baloto.evaluate_ticket_draw(t, official, self.clock.isoformat())
        self.assertEqual(res['prize_cop'], 55_000 + 7_500)
        self.assertEqual(res['lines'][0]['baloto_category'], '3+SB')
        self.assertEqual(res['lines'][0]['revancha_category'], '3')

    def test_creates_cart_consolidates_combos_and_preserves_line_numbers(self):
        t_rev = self.store.create({
            'label': 'Jugada quincena',
            'mode': 'baloto_revancha',
            'draw_count': 3,
            'lines': 'Combo A: 01 02 03 04 05 | 06\nCombo B: 07 08 09 10 11 | 12\nCombo A: 13 14 15 16 17 | 01',
        }, self.clock)
        self.assertEqual(t_rev['start_draw'], 2729)
        self.assertEqual(t_rev['status'], 'cart')
        self.assertEqual(t_rev['price_per_line'], 9000)
        self.assertEqual(t_rev['cost'], 3 * 3 * 9000)
        self.assertEqual(len(t_rev['slips']), 2)
        self.assertEqual(t_rev['slips'][0]['combo'], 'Combo A')
        self.assertEqual(t_rev['slips'][0]['panel_count'], 2)
        self.assertEqual(t_rev['slips'][1]['combo'], 'Combo B')
        self.assertEqual(t_rev['slips'][1]['panel_count'], 1)

        # Leading blank lines must NOT shift reported 1-based line number
        with self.assertRaisesRegex(ValueError, r'Línea 3:'):
            self.baloto.parse_lines('\n\n01 02 03 04 99 | 01')

        # HTML/script characters in combo label must be rejected
        with self.assertRaises(ValueError):
            self.baloto.parse_lines('<img src=x>: 01 02 03 04 05 | 06')

    def test_unsettled_zero_escrutinio_stays_pending_until_settled(self):
        t = self.store.create({
            'label': 'Recién jugado 11pm',
            'mode': 'baloto_revancha',
            'start_draw': 2729,
            'draw_count': 1,
            'already_purchased': True,
            'lines': '01 02 03 04 05 | 06',
        }, self.clock)

        unsettled_pages = {
            'https://baloto.com/resultados': make_resultados_html(sorteo=2729),
            'https://baloto.com/resultados-baloto/2729': make_detalle_html(sorteo=2729, prizes=[0] * 8),
            'https://baloto.com/resultados-revancha/2729': make_detalle_html(
                sorteo=2729, nums=(7, 8, 9, 10, 11), sb=12, prizes=[0] * 8
            ),
        }
        res1 = self.store.sync(fetcher=unsettled_pages.__getitem__, clock=self.clock)
        self.assertEqual(res1['evaluated'], [])
        still_active = self.store.get(t['id'])
        self.assertEqual(still_active['status'], 'active')
        self.assertIsNone(still_active['draws'][0]['result'])

        settled_pages = {
            **unsettled_pages,
            'https://baloto.com/resultados-baloto/2729': make_detalle_html(sorteo=2729),
            'https://baloto.com/resultados-revancha/2729': make_detalle_html(
                sorteo=2729, nums=(7, 8, 9, 10, 11), sb=12
            ),
        }
        res2 = self.store.sync(fetcher=settled_pages.__getitem__, clock=self.clock)
        self.assertEqual(len(res2['evaluated']), 1)
        done = self.store.get(t['id'])
        self.assertEqual(done['status'], 'complete')
        self.assertEqual(done['won'], 4_000_000_000)

    def test_multidraw_lifecycle_warm_cache_and_revancha_cache_upgrade(self):
        # 1. Sync a solo-baloto ticket on draw 2729 (caches baloto only, revancha=None)
        t_solo = self.store.create({
            'label': 'Solo Baloto 2729',
            'mode': 'baloto',
            'start_draw': 2729,
            'draw_count': 1,
            'already_purchased': True,
            'lines': '07 08 09 10 11 | 12',  # 5+SB in Revancha, 0 in Baloto
        }, self.clock)
        pages_2729 = {
            'https://baloto.com/resultados': make_resultados_html(sorteo=2729),
            'https://baloto.com/resultados-baloto/2729': make_detalle_html(sorteo=2729),
            'https://baloto.com/resultados-revancha/2729': make_detalle_html(
                sorteo=2729, nums=(7, 8, 9, 10, 11), sb=12,
                prizes=[1_500_000_000, 30_000_000, 900_000, 90_000, 25_000, 7_500, 6_000, 3_000],
            ),
        }
        self.store.sync(fetcher=pages_2729.__getitem__, clock=self.clock)
        self.assertEqual(self.store.get(t_solo['id'])['won'], 0)

        # 2. Create multi-draw baloto_revancha ticket [2729, 2730]; draw 2729 lacks revancha in cache initially
        t_multi = self.store.create({
            'label': 'Multi 2729-2730',
            'mode': 'baloto_revancha',
            'start_draw': 2728,
            'draw_count': 2,
            'lines': '07 08 09 10 11 | 12',
        }, self.clock)
        # Confirm overriding start_draw -> 2729..2730
        confirmed = self.store.action(t_multi['id'], 'confirm', clock=self.clock, start_draw=2729)
        self.assertEqual([d['sorteo'] for d in confirmed['draws']], [2729, 2730])
        self.assertEqual(confirmed['status'], 'active')

        # Sync at 2729 upgrades cache with revancha and evaluates draw 1 of 2 (partial -> stays 'active')
        self.store.sync(fetcher=pages_2729.__getitem__, clock=self.clock)
        partial = self.store.get(t_multi['id'])
        self.assertEqual(partial['status'], 'active')
        self.assertEqual(partial['played_draws'], 1)
        self.assertEqual(partial['pending_draws'], 1)
        self.assertEqual(partial['won'], 1_500_000_000)

        # Warm-cache test: a newly created already_purchased ticket for 2729 evaluates immediately without sync!
        instant = self.store.create({
            'label': 'Compra retroactiva 2729',
            'mode': 'baloto_revancha',
            'start_draw': 2729,
            'draw_count': 1,
            'already_purchased': True,
            'lines': '01 02 03 04 05 | 06',
        }, self.clock)
        self.assertEqual(instant['status'], 'complete')
        self.assertEqual(instant['won'], 4_000_000_000)

        # Sync draw 2730 -> multi-draw ticket completes!
        pages_2730 = {
            **pages_2729,
            'https://baloto.com/resultados': make_resultados_html(
                sorteo=2730, weekday_txt='Lunes', date_only_txt='2 de Noviembre de 2026',
                baloto=(1, 2, 3, 4, 5), sb_b=6, revancha=(20, 21, 22, 23, 24), sb_r=12,
            ),
            'https://baloto.com/resultados-baloto/2730': make_detalle_html(
                sorteo=2730, weekday_txt='Lunes', date_only_txt='2 de Noviembre de 2026',
                nums=(1, 2, 3, 4, 5), sb=6,
            ),
            'https://baloto.com/resultados-revancha/2730': make_detalle_html(
                sorteo=2730, weekday_txt='Lunes', date_only_txt='2 de Noviembre de 2026',
                nums=(20, 21, 22, 23, 24), sb=12,
                prizes=[1_600_000_000, 30_000_000, 900_000, 90_000, 25_000, 7_500, 6_000, 3_000],
            ),
        }
        self.store.sync(fetcher=pages_2730.__getitem__, clock=self.clock)
        completed = self.store.get(t_multi['id'])
        self.assertEqual(completed['status'], 'complete')
        self.assertEqual(completed['played_draws'], 2)
        self.assertEqual(completed['won'], 1_500_000_000 + 3_000)

    def test_failed_save_symlink_permissions_and_lock_guards(self):
        t = self.store.create({
            'label': 'Rollback test',
            'mode': 'baloto',
            'start_draw': 2729,
            'draw_count': 1,
            'lines': '01 02 03 04 05 | 06',
        }, self.clock)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)

        before = self.store.path.read_text(encoding='utf-8')
        with patch.object(self.store, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.action(t['id'], 'confirm', self.clock)
        self.assertEqual(self.store.get(t['id'])['status'], 'cart')
        self.assertEqual(self.store.path.read_text(encoding='utf-8'), before)

        # Refuse overwriting symlinks in atomic_write_text
        victim = Path(self.temp.name) / 'victim.txt'
        victim.write_text('intacto', encoding='utf-8')
        link = Path(self.temp.name) / 'link.txt'
        try:
            link.symlink_to(victim)
        except OSError:
            pass
        else:
            with self.assertRaises(ValueError):
                self.baloto.atomic_write_text(link, 'sobrescrito')
            self.assertEqual(victim.read_text(encoding='utf-8'), 'intacto')

        with self.baloto.process_lock(self.temp.name):
            with self.assertRaises(ValueError):
                with self.baloto.process_lock(self.temp.name):
                    pass


class _DummyServer:
    server_port = 8765


class _BytesSocket:
    def __init__(self, raw_request: bytes):
        self._rfile = raw_request
        self.output = bytearray()

    def makefile(self, mode: str, *_args, **_kwargs):
        if 'r' in mode:
            return io.BytesIO(self._rfile)
        writer = io.BytesIO()
        orig_close = writer.close

        def flush_and_close():
            self.output.extend(writer.getvalue())
            orig_close()

        writer.close = flush_and_close
        return writer

    def sendall(self, data: bytes):
        self.output.extend(data)


def dispatch_http(handler_cls, method: str, path: str, body: dict | bytes | None = None,
                  headers: dict | None = None) -> tuple[int, dict, str]:
    hdrs = {'Host': '127.0.0.1:8765', **(headers or {})}
    payload_bytes = b''
    if isinstance(body, bytes):
        payload_bytes = body
        hdrs.setdefault('Content-Length', str(len(payload_bytes)))
    elif body is not None:
        payload_bytes = json.dumps(body).encode('utf-8')
        hdrs.setdefault('Content-Type', 'application/json')
        hdrs['Content-Length'] = str(len(payload_bytes))
    header_lines = ''.join(f'{k}: {v}\r\n' for k, v in hdrs.items())
    raw = f'{method} {path} HTTP/1.1\r\n{header_lines}\r\n'.encode('utf-8') + payload_bytes

    sock = _BytesSocket(raw)
    handler = handler_cls(sock, ('127.0.0.1', 55000), _DummyServer())
    if hasattr(handler, 'wfile') and not handler.wfile.closed:
        handler.wfile.flush()
        sock.output.extend(handler.wfile.getvalue())

    head_bytes, _, resp_body = bytes(sock.output).partition(b'\r\n\r\n')
    lines = head_bytes.decode('iso-8859-1').split('\r\n')
    status = int(lines[0].split()[1])
    resp_headers = {}
    for line in lines[1:]:
        if ':' in line:
            k, v = line.split(':', 1)
            resp_headers[k.strip().lower()] = v.strip()
    return status, resp_headers, resp_body.decode('utf-8')


class HttpApiTests(unittest.TestCase):
    def setUp(self):
        self.baloto = load_module('baloto', 'skills/baloto/scripts/baloto.py')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        hist = Path(self.temp.name) / 'historial.csv'
        self.baloto.save_history([
            {'sorteo': 2728, 'fecha': '2026-10-28',
             'baloto': [11, 23, 29, 36, 43], 'sb_baloto': 16,
             'revancha': [17, 30, 33, 34, 37], 'sb_revancha': 12}
        ], hist)
        self.store = self.baloto.Store(self.temp.name, history_path=hist)
        self.token = 'testtoken123456'
        self.pages = {
            'https://baloto.com/resultados': make_resultados_html(sorteo=2729),
            'https://baloto.com/resultados-baloto/2729': make_detalle_html(sorteo=2729),
            'https://baloto.com/resultados-revancha/2729': make_detalle_html(
                sorteo=2729, nums=(7, 8, 9, 10, 11), sb=12
            ),
        }
        self.handler_cls = self.baloto.make_handler(self.store, self.token, fetcher=self.pages.__getitem__)

    def test_http_endpoints_csp_nonce_delete_and_status_codes(self):
        code, _, body = dispatch_http(self.handler_cls, 'GET', '/api/health')
        self.assertEqual(code, 200)
        health = json.loads(body)
        self.assertTrue(health['ok'])
        self.assertEqual(health['next_sorteo'], 2729)
        self.assertTrue(health['carrito_helper_path'].endswith('carrito_helper.py'))

        # Security headers & cross-origin rejections (403)
        self.assertEqual(dispatch_http(self.handler_cls, 'GET', '/api/health', headers={'Host': 'evil:8765'})[0], 403)
        self.assertEqual(
            dispatch_http(self.handler_cls, 'GET', '/api/tickets', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403
        )
        self.assertEqual(
            dispatch_http(self.handler_cls, 'POST', '/api/tickets', body={'lines': '01 02 03 04 05 | 06'})[0], 403
        )
        self.assertEqual(
            dispatch_http(
                self.handler_cls, 'POST', '/api/tickets',
                body={'lines': '01 02 03 04 05 | 06'},
                headers={'X-CSRF-Token': self.token, 'Origin': 'https://evil.example'},
            )[0],
            403,
        )

        auth = {'X-CSRF-Token': self.token}
        # 413 Payload Too Large & 415 Unsupported Media Type
        self.assertEqual(
            dispatch_http(
                self.handler_cls, 'POST', '/api/tickets',
                body=b'x' * 140_000,
                headers={**auth, 'Content-Type': 'application/json'},
            )[0],
            413,
        )
        self.assertEqual(
            dispatch_http(
                self.handler_cls, 'POST', '/api/tickets',
                body=b'plain',
                headers={**auth, 'Content-Type': 'text/plain'},
            )[0],
            415,
        )

        # Case-insensitive Content-Type with charset succeeds (201)
        code_create, _, created_raw = dispatch_http(
            self.handler_cls, 'POST', '/api/tickets',
            body={
                'label': 'Tiquete API',
                'mode': 'baloto_revancha',
                'start_draw': 2729,
                'draw_count': 1,
                'already_purchased': True,
                'lines': '01 02 03 04 05 | 06',
            },
            headers={**auth, 'Content-Type': 'Application/JSON; charset=utf-8'},
        )
        self.assertEqual(code_create, 201)
        created = json.loads(created_raw)

        # Empty body (Content-Length: 0) on POST /api/sync succeeds
        code_sync, _, _ = dispatch_http(self.handler_cls, 'POST', '/api/sync', body=None, headers=auth)
        self.assertEqual(code_sync, 200)

        code_det, _, det_raw = dispatch_http(self.handler_cls, 'GET', f"/api/tickets/{created['id']}")
        self.assertEqual(code_det, 200)
        detail = json.loads(det_raw)
        self.assertEqual(detail['status'], 'complete')
        self.assertEqual(detail['won'], 4_000_000_000)

        # Per-response CSP nonce must be distinct from CSRF token and rotate per response
        code_h1, hdrs1, html1 = dispatch_http(self.handler_cls, 'GET', '/estadisticas')
        code_h2, hdrs2, _ = dispatch_http(self.handler_cls, 'GET', '/')
        self.assertEqual(code_h1, 200)
        self.assertEqual(code_h2, 200)
        m1 = re.search(r"'nonce-([a-f0-9]{32})'", hdrs1['content-security-policy'])
        m2 = re.search(r"'nonce-([a-f0-9]{32})'", hdrs2['content-security-policy'])
        self.assertIsNotNone(m1)
        self.assertIsNotNone(m2)
        self.assertNotEqual(m1.group(1), m2.group(1))
        self.assertNotEqual(m1.group(1), self.token)
        self.assertIn(f'<script nonce="{m1.group(1)}">', html1)
        self.assertIn(f"const TOKEN='{self.token}';", html1)

        # RESTful DELETE /api/tickets/<id> + 404 on missing ticket
        code_del, _, _ = dispatch_http(self.handler_cls, 'DELETE', f"/api/tickets/{created['id']}", headers=auth)
        self.assertEqual(code_del, 200)
        self.assertEqual(dispatch_http(self.handler_cls, 'GET', f"/api/tickets/{created['id']}")[0], 404)

    def test_http_502_on_timeout_urlerror_and_500_on_disk_oserror(self):
        auth = {'X-CSRF-Token': self.token}
        for net_exc in (TimeoutError('timed out'), URLError('dns failure'), self.baloto.UpstreamSyncError('bad html')):
            cls = self.baloto.make_handler(self.store, self.token, fetcher=lambda _u, e=net_exc: (_ for _ in ()).throw(e))
            code, _, body = dispatch_http(cls, 'POST', '/api/sync', headers=auth)
            self.assertEqual(code, 502)
            self.assertIn('baloto.com', json.loads(body)['error'])

        with patch.object(self.store, 'save', side_effect=OSError('io error')):
            code_500, _, _ = dispatch_http(
                self.handler_cls, 'POST', '/api/tickets',
                body={'label': 'Falla disco', 'lines': '01 02 03 04 05 | 06'},
                headers=auth,
            )
            self.assertEqual(code_500, 500)

    def test_live_tcp_server(self):
        # Real loopback TCP test (runs in CI; gracefully skipped only if OS sandbox blocks sockets)
        try:
            server = self.baloto.ThreadingHTTPServer(('127.0.0.1', 0), self.handler_cls)
        except PermissionError as exc:
            self.skipTest(f'Sandbox restringe puertos locales: {exc}')
        with server:
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                try:
                    conn.request('GET', '/api/health', headers={'Host': f'127.0.0.1:{server.server_port}'})
                except PermissionError as exc:
                    conn.close()
                    self.skipTest(f'Sandbox restringe conexiones loopback: {exc}')
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode('utf-8'))
                self.assertTrue(data['ok'])
                conn.close()
            finally:
                server.shutdown()
                t.join(timeout=3)


class CartHelperAndCliTests(unittest.TestCase):
    def test_splits_boletas_escapes_dom_xss_and_validates_stored_dicts(self):
        helper = load_module('carrito_helper', 'skills/baloto/scripts/carrito_helper.py')
        self.assertEqual(helper.PLAY_URL, 'https://apuestaaqui.baloto.com/')
        lines = '\n'.join(f'{i:02d} {i+1:02d} {i+2:02d} {i+3:02d} {i+4:02d} | {(i % 16) + 1:02d}'
                          for i in range(1, 8))
        plan = helper.build_cart_plan({
            'label': 'Jugada </script> segura',
            'mode': 'baloto_revancha',
            'start_draw': 2729,
            'draw_count': 2,
            'lines': lines,
        })
        self.assertTrue(plan['stop_before_payment'])
        self.assertEqual(len(plan['slips']), 2)
        self.assertEqual([p['panel'] for p in plan['slips'][0]['panels']], ['A', 'B', 'C', 'D', 'E'])
        self.assertEqual([p['panel'] for p in plan['slips'][1]['panels']], ['A', 'B'])
        self.assertEqual(plan['total_cost_cop'], 7 * 2 * 9000)

        js_code = helper.generate_browser_snippet(plan)
        self.assertIn('STOP_BEFORE_CHECKOUT', js_code)
        self.assertNotIn('innerHTML', js_code)
        self.assertNotIn('</script>', js_code)
        self.assertIn(r'<\/script>', js_code)
        self.assertNotIn('password', js_code.lower())

        # If Node.js is available, execute generated JS against a mock DOM to verify forbidden-target guardrails
        node_bin = shutil.which('node')
        if node_bin:
            probe = f"""
            const children = [];
            const mk = tag => ({{
              tag, style: {{}}, children: [], textContent: '',
              setAttribute() {{}}, addEventListener() {{}},
              appendChild(c) {{ this.children.push(c); return c; }},
              replaceChildren(...cs) {{ this.children = cs; }},
              remove() {{}}
            }});
            globalThis.window = {{}};
            globalThis.document = {{
              getElementById: () => null,
              createElement: mk,
              createTextNode: t => ({{ textContent: t }}),
              body: {{ appendChild(el) {{ children.push(el); }} }}
            }};
            const res = {js_code};
            const btnContinuePse = {{ innerText: 'Continuar', id: 'btn-pagar-pse', closest: () => null }};
            const spanInsidePay = {{ innerText: 'Ir ahora', id: '', closest: () => ({{ id: 'checkout-form', textContent: '' }}) }};
            const panelBall = {{ innerText: '23', id: 'ball-23', closest: () => ({{ id: 'panel-a', textContent: 'Panel A' }}) }};
            if (!res.ok || !res.stopBeforeCheckout) process.exit(2);
            if (!window.__balotoForbiddenTarget(btnContinuePse)) process.exit(3);
            if (!window.__balotoForbiddenTarget(spanInsidePay)) process.exit(4);
            if (window.__balotoForbiddenTarget(panelBall)) process.exit(5);
            """
            subprocess.run([node_bin, '-e', probe], check=True, timeout=10)

        # Stored-dict shape with tampered combo or out-of-range numbers MUST be rejected
        with self.assertRaises(ValueError):
            helper.build_cart_plan({
                'label': 'Tampered',
                'mode': 'baloto',
                'start_draw': 2729,
                'draw_count': 1,
                'price_per_line': 6000,
                'draws': [],
                'lines': [{'numbers': [1, 2, 3, 4, 5], 'extra': 6, 'combo': '<img onerror=alert(1)>'}],
            })

    def test_cli_stdin_confirm_and_cp1252_windows_encoding_resilience(self):
        env = {**os.environ, 'PYTHONIOENCODING': 'cp1252'}
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / 'store'
            payload_str = json.dumps({
                'label': 'Rueda stdin',
                'mode': 'baloto_revancha',
                'start_draw': 2729,
                'draw_count': 2,
                'lines': 'Combo A: 01 12 23 34 41 | 05\nCombo B: 02 13 24 35 42 | 09',
            })

            prep = subprocess.run(
                [sys.executable, str(ROOT / 'skills/baloto/scripts/baloto.py'),
                 '--data-dir', str(data_dir), '--prepare', '-', '--no-serve'],
                input=payload_str.encode('utf-8'),
                capture_output=True,
                env=env,
                check=True,
            )
            created = json.loads(prep.stdout.decode('utf-8'))
            self.assertEqual(created['status'], 'cart')
            self.assertEqual(created['cost'], 36_000)

            # Confirm from CLI with --start-draw override under cp1252
            conf = subprocess.run(
                [sys.executable, str(ROOT / 'skills/baloto/scripts/baloto.py'),
                 '--data-dir', str(data_dir), '--confirm', created['id'], '--start-draw', '2730'],
                capture_output=True,
                env=env,
                check=True,
            )
            self.assertIn('#2730→#2731', conf.stdout.decode('utf-8'))

            plan_out = subprocess.run(
                [sys.executable, str(ROOT / 'skills/baloto/scripts/carrito_helper.py'),
                 '--data-dir', str(data_dir), '--ticket-id', created['id'], '--mode', 'plan'],
                capture_output=True,
                env=env,
                check=True,
            )
            plan_txt = plan_out.stdout.decode('utf-8')
            self.assertIn('Boleta #1 (Combo A)', plan_txt)
            self.assertIn('apuestaaqui.baloto.com', plan_txt)
            self.assertIn('$36.000', plan_txt)

            stats_txt = subprocess.run(
                [sys.executable, str(ROOT / 'skills/baloto/scripts/baloto.py'),
                 '--stats', '--game', 'revancha', '--last', '30'],
                capture_output=True,
                env=env,
                check=True,
            )
            self.assertIn('Chi² balotas', stats_txt.stdout.decode('utf-8'))

    def test_malformed_official_html_never_mutates_history_or_ticket_state(self):
        baloto = load_module('baloto', 'skills/baloto/scripts/baloto.py')
        with tempfile.TemporaryDirectory() as tmp:
            hist = Path(tmp) / 'historial.csv'
            baloto.save_history([
                {'sorteo': 2728, 'fecha': '2026-10-28',
                 'baloto': [11, 23, 29, 36, 43], 'sb_baloto': 16,
                 'revancha': [17, 30, 33, 34, 37], 'sb_revancha': 12}
            ], hist)
            before_csv = hist.read_text(encoding='utf-8')
            store = baloto.Store(tmp, history_path=hist)
            t = store.create({
                'label': 'Protegido',
                'mode': 'baloto_revancha',
                'start_draw': 2729,
                'draw_count': 1,
                'already_purchased': True,
                'lines': '01 02 03 04 05 | 06',
            })
            bad_pages = {
                'https://baloto.com/resultados': make_resultados_html(sorteo=2729),
                'https://baloto.com/resultados-baloto/2729': '<html><body>tabla cambiada</body></html>',
            }
            with self.assertRaises(ValueError):
                store.sync(fetcher=bad_pages.__getitem__)
            self.assertIsNone(store.get(t['id'])['draws'][0]['result'])
            self.assertEqual(hist.read_text(encoding='utf-8'), before_csv)


class InstallerTests(unittest.TestCase):
    def test_individual_agents_force_csv_merge_and_symlink_protection(self):
        baloto = load_module('baloto', 'skills/baloto/scripts/baloto.py')
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            script = str(ROOT / 'scripts/install.py')

            # Reject deprecated 'both'
            bad = subprocess.run([sys.executable, script, '--agent', 'both', '--home', str(home)],
                                 capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(bad.returncode, 0)

            # Individual agent install: only antigravity is created
            res_agy = subprocess.run(
                [sys.executable, script, '--agent', 'antigravity', '--home', str(home)],
                capture_output=True, text=True, encoding='utf-8', check=True,
            )
            agy_dir = home / '.gemini/config/skills/baloto'
            claude_dir = home / '.claude/skills/baloto'
            codex_dir = home / '.agents/skills/baloto'
            self.assertTrue((agy_dir / 'SKILL.md').is_file())
            self.assertFalse(claude_dir.exists())
            self.assertFalse(codex_dir.exists())
            self.assertIn(str(agy_dir), res_agy.stdout)

            # Simulate user having synced draw #9999 locally in installed CSV, then run --agent all --force
            installed_csv = agy_dir / 'data/historial_baloto.csv'
            rows = baloto.load_history(installed_csv)
            rows.append({
                'sorteo': 9999,
                'fecha': '2029-12-31',
                'baloto': [1, 2, 3, 4, 5],
                'sb_baloto': 6,
                'revancha': [7, 8, 9, 10, 11],
                'sb_revancha': 12,
            })
            baloto.save_history(rows, installed_csv)

            # Without --force, refuses to overwrite
            no_force = subprocess.run([sys.executable, script, '--agent', 'all', '--home', str(home)],
                                      capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(no_force.returncode, 0)

            # With --force, installs all 3 and preserves draw #9999 in antigravity!
            subprocess.run([sys.executable, script, '--agent', 'all', '--home', str(home), '--force'],
                           capture_output=True, text=True, encoding='utf-8', check=True)
            for target in (agy_dir, claude_dir, codex_dir):
                self.assertTrue((target / 'SKILL.md').is_file())
            merged_rows = baloto.load_history(installed_csv)
            self.assertEqual(merged_rows[-1]['sorteo'], 9999)

            # Ownership & symlink protection: refuses to delete foreign folder or symlink even with --force
            foreign_home = Path(tmp) / 'foreign_home'
            foreign_home.mkdir()
            foreign_target = foreign_home / '.claude/skills/baloto'
            foreign_target.mkdir(parents=True)
            (foreign_target / 'SKILL.md').write_text('---\nname: otra-skill\n---\n', encoding='utf-8')
            refused = subprocess.run(
                [sys.executable, script, '--agent', 'claude', '--home', str(foreign_home), '--force'],
                capture_output=True, text=True, encoding='utf-8',
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertTrue((foreign_target / 'SKILL.md').exists())


if __name__ == '__main__':
    unittest.main()
