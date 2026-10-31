from contextlib import contextmanager
from datetime import datetime, timezone
import http.client
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, rel_path: str):
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f'No se pudo cargar {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_resultados_html(sorteo=2729, fecha_txt='Sábado 31 de Octubre de 2026',
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
      <p>{fecha_txt}</p>
      {''.join(balls_html)}
    </body></html>
    """


def make_detalle_html(sorteo=2729, fecha_txt='Sábado 31 de Octubre de 2026',
                      nums=(1, 2, 3, 4, 5), sb=6, prizes=None):
    default_prizes = [
        4_000_000_000,  # 5 + SB
        85_000_000,     # 5
        2_500_000,      # 4 + SB
        150_000,        # 4
        55_000,         # 3 + SB
        12_000,         # 3
        10_500,         # 2 + SB
        6_000,          # 0 o 1 + SB
    ]
    values = prizes if prizes is not None else default_prizes
    balls_html = [f'<div class="yellow-ball">{n:02d}</div>' for n in nums]
    balls_html.append(f'<div class="red-ball">{sb:02d}</div>')
    rows_html = []
    for val in values:
        fmt = f'${val:,}'.replace(',', '.')
        rows_html.append(
            '<tr class="br-header-left br-header-right">'
            f'<td class="dark-blue">{fmt}</td>'
            '<td class="dark-blue">1</td>'
            f'<td class="dark-blue">{fmt}</td>'
            '</tr>'
        )
    return f"""
    <html><body>
      <h1>Resultado sorteo #{sorteo}</h1>
      <p>{fecha_txt}</p>
      {''.join(balls_html)}
      <table>
        <thead><tr><th>PREMIO TOTAL</th></tr></thead>
        <tbody>{''.join(rows_html)}</tbody>
      </table>
      <div class="next-contest"></div>
    </body></html>
    """


class HistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baloto = load_module('baloto', 'skills/baloto/scripts/baloto.py')

    def test_bundled_history_has_complete_verified_draws(self):
        rows = self.baloto.load_history()
        self.assertGreaterEqual(len(rows), 369)
        self.assertEqual(rows[0]['sorteo'], 2360)
        self.assertEqual(rows[0]['fecha'], '2024-01-03')
        sorteos = [r['sorteo'] for r in rows]
        self.assertEqual(sorteos, list(range(rows[0]['sorteo'], rows[-1]['sorteo'] + 1)))
        for row in rows:
            self.assertEqual(len(set(row['baloto'])), 5)
            self.assertEqual(row['baloto'], sorted(row['baloto']))
            self.assertTrue(all(1 <= n <= 43 for n in row['baloto']))
            self.assertTrue(1 <= row['sb_baloto'] <= 16)
            self.assertEqual(len(set(row['revancha'])), 5)
            self.assertTrue(1 <= row['sb_revancha'] <= 16)

    def test_compute_stats_frequencies_chi2_lags_and_window(self):
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
        self.assertEqual(stats['chi2_numbers']['df'], 42)
        self.assertEqual(stats['chi2_superbalota']['df'], 15)
        self.assertEqual(stats['top_pairs'][0]['pair'], [1, 2])
        self.assertEqual(stats['top_pairs'][0]['count'], 2)

        win_stats = self.baloto.compute_stats(sample, game='baloto', last=2)
        self.assertEqual(win_stats['draw_count'], 2)
        self.assertEqual(win_stats['from_sorteo'], 2702)

        both_stats = self.baloto.compute_stats(sample, game='both')
        self.assertEqual(both_stats['sample_draws'], 6)

    def test_sync_history_appends_missing_draws_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / 'historial_baloto.csv'
            self.baloto.save_history([
                {'sorteo': 2728, 'fecha': '2026-10-28',
                 'baloto': [11, 23, 29, 36, 43], 'sb_baloto': 16,
                 'revancha': [17, 30, 33, 34, 37], 'sb_revancha': 12}
            ], csv_path)

            pages = {
                'https://baloto.com/resultados': make_resultados_html(
                    sorteo=2730, fecha_txt='Lunes 2 de Noviembre de 2026',
                    baloto=(3, 14, 21, 35, 42), sb_b=9,
                    revancha=(5, 12, 19, 28, 40), sb_r=4,
                ),
                'https://baloto.com/resultados-baloto/2729': make_detalle_html(
                    sorteo=2729, fecha_txt='Sábado 31 de Octubre de 2026',
                    nums=(1, 2, 3, 4, 5), sb=6,
                ),
                'https://baloto.com/resultados-revancha/2729': make_detalle_html(
                    sorteo=2729, fecha_txt='Sábado 31 de Octubre de 2026',
                    nums=(7, 8, 9, 10, 11), sb=12,
                ),
            }
            summary = self.baloto.sync_history(csv_path, fetcher=pages.__getitem__)
            self.assertEqual(summary['added'], [2729, 2730])
            rows = self.baloto.load_history(csv_path)
            self.assertEqual([r['sorteo'] for r in rows], [2728, 2729, 2730])
            self.assertEqual(rows[-1]['fecha'], '2026-11-02')

            again = self.baloto.sync_history(csv_path, fetcher=pages.__getitem__)
            self.assertEqual(again['added'], [])


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

    def test_creates_cart_with_real_cop_prices_for_both_modes(self):
        t_rev = self.store.create({
            'label': 'Jugada quincena',
            'mode': 'baloto_revancha',
            'start_draw': 2729,
            'draw_count': 3,
            'lines': 'A: 01 02 03 04 05 | 06\nA: 07 08 09 10 11 | 12',
        }, self.clock)
        self.assertEqual(t_rev['status'], 'cart')
        self.assertEqual(t_rev['price_per_line'], 9000)
        self.assertEqual(t_rev['cost'], 2 * 3 * 9000)
        self.assertEqual(t_rev['spent'], 0)
        self.assertEqual([d['sorteo'] for d in t_rev['draws']], [2729, 2730, 2731])

        t_solo = self.store.create({
            'label': 'Solo Baloto sábado',
            'mode': 'baloto',
            'start_draw': 2729,
            'draw_count': 1,
            'lines': '04 15 23 34 41 | 08',
        }, self.clock)
        self.assertEqual(t_solo['price_per_line'], 6000)
        self.assertEqual(t_solo['cost'], 6000)

    def test_rejects_invalid_duplicate_and_malformed_inputs(self):
        base = {
            'label': 'Prueba',
            'mode': 'baloto_revancha',
            'start_draw': 2729,
            'draw_count': 1,
            'lines': '01 02 03 04 05 | 06',
        }
        for bad_lines in [
            '',
            '1 1 2 3 4 | 1',
            '1 2 3 4 44 | 1',
            '1 2 3 4 5 | 17',
            '1 2 3 4 5',
            '01 02 03 04 05 | 06\n05 04 03 02 01 | 06',
        ]:
            with self.subTest(bad_lines=bad_lines), self.assertRaises(ValueError):
                self.store.create({**base, 'lines': bad_lines}, self.clock)

        for bad_fields in [
            {'mode': 'simulador'},
            {'draw_count': 0},
            {'draw_count': True},
            {'price_per_line': 500},
            {'unknown': 123},
        ]:
            with self.subTest(bad_fields=bad_fields), self.assertRaises(ValueError):
                self.store.create({**base, **bad_fields}, self.clock)

    def test_sync_calculates_exact_official_cop_prizes_and_completes(self):
        ticket = self.store.create({
            'label': 'Tiquete real S2729',
            'mode': 'baloto_revancha',
            'start_draw': 2729,
            'draw_count': 1,
            'already_purchased': True,
            'lines': '\n'.join([
                '01 02 03 20 21 | 06',  # Baloto: 3 + SB ($55.000) | Revancha: 0
                '30 31 32 33 34 | 06',  # Baloto: 0 + SB ($6.000)  | Revancha: 0
                '01 30 31 32 33 | 06',  # Baloto: 1 + SB ($6.000)  | Revancha: 0
                '07 08 09 10 40 | 01',  # Baloto: 0                | Revancha: 4 sin SB ($90.000)
                '40 41 42 43 15 | 01',  # Sin premio ($0)
            ]),
        }, self.clock)
        self.assertEqual(ticket['status'], 'active')
        self.assertEqual(ticket['spent'], 45_000)

        pages = {
            'https://baloto.com/resultados': make_resultados_html(
                sorteo=2729, fecha_txt='Sábado 31 de Octubre de 2026',
                baloto=(1, 2, 3, 4, 5), sb_b=6,
                revancha=(7, 8, 9, 10, 11), sb_r=12,
            ),
            'https://baloto.com/resultados-baloto/2729': make_detalle_html(
                sorteo=2729, fecha_txt='Sábado 31 de Octubre de 2026',
                nums=(1, 2, 3, 4, 5), sb=6,
                prizes=[4_000_000_000, 85_000_000, 2_500_000, 150_000, 55_000, 12_000, 10_500, 6_000],
            ),
            'https://baloto.com/resultados-revancha/2729': make_detalle_html(
                sorteo=2729, fecha_txt='Sábado 31 de Octubre de 2026',
                nums=(7, 8, 9, 10, 11), sb=12,
                prizes=[1_500_000_000, 30_000_000, 900_000, 90_000, 25_000, 7_500, 6_000, 3_000],
            ),
        }
        sync_res = self.store.sync(fetcher=pages.__getitem__, clock=self.clock)
        self.assertEqual(sync_res['latest_sorteo'], 2729)
        updated = self.store.get(ticket['id'])
        self.assertEqual(updated['status'], 'complete')
        self.assertEqual(updated['won'], 55_000 + 6_000 + 6_000 + 90_000)
        self.assertEqual(updated['balance'], 157_000 - 45_000)

        # Re-syncing is strictly idempotent
        self.store.sync(fetcher=pages.__getitem__, clock=self.clock)
        self.assertEqual(self.store.get(ticket['id'])['won'], 157_000)

    def test_solo_baloto_ignores_revancha_prizes(self):
        ticket = self.store.create({
            'label': 'Solo Baloto',
            'mode': 'baloto',
            'start_draw': 2729,
            'draw_count': 1,
            'already_purchased': True,
            'lines': '07 08 09 10 11 | 12',  # 5+SB en Revancha, 0 en Baloto
        }, self.clock)
        pages = {
            'https://baloto.com/resultados': make_resultados_html(
                sorteo=2729, baloto=(1, 2, 3, 4, 5), sb_b=6, revancha=(7, 8, 9, 10, 11), sb_r=12
            ),
            'https://baloto.com/resultados-baloto/2729': make_detalle_html(
                sorteo=2729, nums=(1, 2, 3, 4, 5), sb=6
            ),
        }
        self.store.sync(fetcher=pages.__getitem__, clock=self.clock)
        updated = self.store.get(ticket['id'])
        self.assertEqual(updated['won'], 0)
        self.assertEqual(updated['balance'], -6_000)

    def test_cart_is_not_evaluated_until_confirmed_and_can_be_deleted(self):
        ticket = self.store.create({
            'label': 'Borrador carrito',
            'mode': 'baloto_revancha',
            'start_draw': 2729,
            'draw_count': 1,
            'lines': '01 02 03 04 05 | 06',
        }, self.clock)
        pages = {
            'https://baloto.com/resultados': make_resultados_html(sorteo=2729),
            'https://baloto.com/resultados-baloto/2729': make_detalle_html(sorteo=2729),
            'https://baloto.com/resultados-revancha/2729': make_detalle_html(sorteo=2729, nums=(7, 8, 9, 10, 11), sb=12),
        }
        self.store.sync(fetcher=pages.__getitem__, clock=self.clock)
        self.assertIsNone(self.store.get(ticket['id'])['draws'][0]['result'])

        self.store.action(ticket['id'], 'confirm', clock=self.clock)
        self.store.sync(fetcher=pages.__getitem__, clock=self.clock)
        self.assertEqual(self.store.get(ticket['id'])['won'], 4_000_000_000)

        self.store.delete(ticket['id'])
        self.assertEqual(self.store.list(), [])

    def test_failed_save_rolls_back_and_lock_guards_directory(self):
        t = self.store.create({
            'label': 'Rollback test',
            'mode': 'baloto',
            'start_draw': 2729,
            'draw_count': 1,
            'lines': '01 02 03 04 05 | 06',
        }, self.clock)
        before = self.store.path.read_text(encoding='utf-8')
        with patch.object(self.store, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.action(t['id'], 'confirm', self.clock)
        self.assertEqual(self.store.get(t['id'])['status'], 'cart')
        self.assertEqual(self.store.path.read_text(encoding='utf-8'), before)

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
        import io
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


def dispatch_http(handler_cls, method: str, path: str, body: dict | None = None,
                  headers: dict | None = None) -> tuple[int, dict, str]:
    hdrs = {'Host': '127.0.0.1:8765', **(headers or {})}
    payload_bytes = b''
    if body is not None:
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
        pages = {
            'https://baloto.com/resultados': make_resultados_html(sorteo=2729),
            'https://baloto.com/resultados-baloto/2729': make_detalle_html(sorteo=2729),
            'https://baloto.com/resultados-revancha/2729': make_detalle_html(
                sorteo=2729, nums=(7, 8, 9, 10, 11), sb=12
            ),
        }
        self.handler_cls = self.baloto.make_handler(self.store, self.token, fetcher=pages.__getitem__)

    def test_http_endpoints_and_csrf_host_protection(self):
        code, _, body = dispatch_http(self.handler_cls, 'GET', '/api/health')
        self.assertEqual(code, 200)
        health = json.loads(body)
        self.assertTrue(health['ok'])
        self.assertEqual(health['next_sorteo'], 2729)

        # Reject unauthorized Host header
        code_host, _, _ = dispatch_http(
            self.handler_cls, 'GET', '/api/health', headers={'Host': 'evil.example:8765'}
        )
        self.assertEqual(code_host, 403)

        # Reject missing CSRF token
        code_csrf, _, _ = dispatch_http(
            self.handler_cls, 'POST', '/api/tickets',
            body={'label': 'X', 'lines': '01 02 03 04 05 | 06'},
        )
        self.assertEqual(code_csrf, 403)

        # Reject foreign Origin
        code_origin, _, _ = dispatch_http(
            self.handler_cls, 'POST', '/api/tickets',
            body={'label': 'X', 'lines': '01 02 03 04 05 | 06'},
            headers={'X-CSRF-Token': self.token, 'Origin': 'https://evil.example'},
        )
        self.assertEqual(code_origin, 403)

        auth = {'X-CSRF-Token': self.token}
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
            headers=auth,
        )
        self.assertEqual(code_create, 201)
        created = json.loads(created_raw)

        code_sync, _, _ = dispatch_http(self.handler_cls, 'POST', '/api/sync', body={}, headers=auth)
        self.assertEqual(code_sync, 200)

        code_det, _, det_raw = dispatch_http(self.handler_cls, 'GET', f"/api/tickets/{created['id']}")
        self.assertEqual(code_det, 200)
        detail = json.loads(det_raw)
        self.assertEqual(detail['status'], 'complete')
        self.assertEqual(detail['won'], 4_000_000_000)

        code_st, _, st_raw = dispatch_http(self.handler_cls, 'GET', '/api/history/stats?game=baloto')
        self.assertEqual(code_st, 200)
        stats = json.loads(st_raw)
        self.assertEqual(stats['draw_count'], 2)

        code_html, hdrs_html, html_body = dispatch_http(self.handler_cls, 'GET', '/estadisticas')
        self.assertEqual(code_html, 200)
        self.assertIn(f"nonce-{self.token}", hdrs_html['content-security-policy'])
        self.assertIn(self.token, html_body)

        code_del, _, _ = dispatch_http(
            self.handler_cls, 'POST', f"/api/tickets/{created['id']}/delete", body={}, headers=auth
        )
        self.assertEqual(code_del, 200)


class CartHelperTests(unittest.TestCase):
    def test_splits_lines_into_five_panel_boletas_and_stops_before_payment(self):
        helper = load_module('carrito_helper', 'skills/baloto/scripts/carrito_helper.py')
        lines = '\n'.join(f'{i:02d} {i+1:02d} {i+2:02d} {i+3:02d} {i+4:02d} | {(i % 16) + 1:02d}'
                          for i in range(1, 8))
        plan = helper.build_cart_plan({
            'label': 'Siete líneas',
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
        self.assertNotIn('password', js_code.lower())

    def test_cli_prepare_and_cart_helper_from_ticket_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / 'store'
            jugada_json = Path(tmp) / 'jugada.json'
            jugada_json.write_text(json.dumps({
                'label': 'Rueda CLI',
                'mode': 'baloto_revancha',
                'start_draw': 2729,
                'draw_count': 2,
                'lines': 'Combo A: 01 12 23 34 41 | 05\nCombo B: 02 13 24 35 42 | 09',
            }), encoding='utf-8')

            prep = subprocess.run(
                [sys.executable, str(ROOT / 'skills/baloto/scripts/baloto.py'),
                 '--data-dir', str(data_dir), '--prepare', str(jugada_json), '--no-serve'],
                capture_output=True, text=True, check=True,
            )
            created = json.loads(prep.stdout)
            self.assertEqual(created['status'], 'cart')
            self.assertEqual(created['cost'], 36_000)

            plan_out = subprocess.run(
                [sys.executable, str(ROOT / 'skills/baloto/scripts/carrito_helper.py'),
                 '--data-dir', str(data_dir), '--ticket-id', created['id'], '--mode', 'plan'],
                capture_output=True, text=True, check=True,
            )
            self.assertIn('Boleta #1 (Combo A)', plan_out.stdout)
            self.assertIn('Boleta #2 (Combo B)', plan_out.stdout)
            self.assertIn('$36.000', plan_out.stdout)

            stats_out = subprocess.run(
                [sys.executable, str(ROOT / 'skills/baloto/scripts/baloto.py'),
                 '--stats', '--game', 'both', '--last', '50', '--json'],
                capture_output=True, text=True, check=True,
            )
            stats_data = json.loads(stats_out.stdout)
            self.assertEqual(stats_data['draw_count'], 50)
            self.assertEqual(stats_data['sample_draws'], 100)

    def test_malformed_official_html_never_corrupts_history_or_ticket_state(self):
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
            self.assertTrue(before_csv.splitlines()[0] in hist.read_text(encoding='utf-8'))


class InstallerTests(unittest.TestCase):
    def test_installs_to_antigravity_claude_and_codex_and_guards_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cmd = [sys.executable, str(ROOT / 'scripts/install.py'), '--agent', 'all', '--home', str(home)]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            expected_targets = [
                home / '.gemini/config/skills/baloto',
                home / '.claude/skills/baloto',
                home / '.agents/skills/baloto',
            ]
            for target in expected_targets:
                self.assertTrue((target / 'SKILL.md').exists())
                self.assertTrue((target / 'data/historial_baloto.csv').exists())
                self.assertIn(str(target), res.stdout)

            # Without --force, refuses to overwrite
            again = subprocess.run(cmd, capture_output=True, text=True)
            self.assertNotEqual(again.returncode, 0)

            # With --force, refreshes cleanly
            forced = subprocess.run([*cmd, '--force'], capture_output=True, text=True)
            self.assertEqual(forced.returncode, 0)


if __name__ == '__main__':
    unittest.main()
