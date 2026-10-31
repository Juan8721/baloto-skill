#!/usr/bin/env python3
"""Local educational simulator. No outbound network, real bets or payments."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import random
import re
import secrets
import signal
import sys
import tempfile
import threading
from urllib.parse import urlsplit
import uuid

UTC = timezone.utc
DAY = timedelta(days=1)
# Invented demonstration credits. These are not official prize values.
PRIZES = {(5, True): 1000, (5, False): 100, (4, True): 50,
          (4, False): 20, (3, True): 10, (3, False): 5,
          (2, True): 3, (1, True): 1, (0, True): 1}


def now():
    return datetime.now(UTC)


def integer(value, field, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{field}: debe ser un entero entre {low} y {high}.')
    return value


def parse_lines(value):
    if not isinstance(value, str) or len(value) > 30000:
        raise ValueError('Introduce las líneas como texto (máximo 30.000 caracteres).')
    lines, seen = [], set()
    for index, raw in enumerate(value.strip().splitlines(), 1):
        if not raw.strip():
            continue
        parts = raw.split('|')
        if len(parts) != 2:
            raise ValueError(f'Línea {index}: usa cinco números | un número adicional.')
        pieces = re.split(r'[\s,\-]+', parts[0].strip())
        if len(pieces) != 5 or not all(re.fullmatch(r'\d{1,2}', p) for p in pieces):
            raise ValueError(f'Línea {index}: se necesitan cinco números.')
        numbers = sorted(map(int, pieces))
        if len(set(numbers)) != 5 or not all(1 <= n <= 43 for n in numbers):
            raise ValueError(f'Línea {index}: cinco números distintos entre 1 y 43.')
        if not re.fullmatch(r'\d{1,2}', parts[1].strip()):
            raise ValueError(f'Línea {index}: número adicional inválido.')
        extra = int(parts[1].strip())
        integer(extra, f'Línea {index}, número adicional', 1, 16)
        key = tuple(numbers) + (extra,)
        if key in seen:
            raise ValueError(f'Línea {index}: combinación duplicada.')
        seen.add(key)
        lines.append({'numbers': numbers, 'extra': extra})
    if not 1 <= len(lines) <= 500:
        raise ValueError('Introduce entre 1 y 500 líneas de ejemplo.')
    return lines


def make_campaign(payload, clock):
    if not isinstance(payload, dict):
        raise ValueError('La entrada debe ser un objeto JSON.')
    allowed = {'label', 'budget', 'draw_count', 'interval_days', 'lines'}
    if set(payload) - allowed:
        raise ValueError('La entrada contiene campos no reconocidos.')
    label = payload.get('label', 'Mi prueba')
    if not isinstance(label, str) or not label.strip() or len(label) > 80:
        raise ValueError('El nombre debe tener entre 1 y 80 caracteres.')
    budget = integer(payload.get('budget'), 'Créditos', 1, 1000000)
    count = integer(payload.get('draw_count'), 'Sorteos', 1, 100)
    interval = integer(payload.get('interval_days'), 'Intervalo', 1, 30)
    lines = parse_lines(payload.get('lines'))
    cost = count * len(lines)
    if cost > 5000:
        raise ValueError('El máximo de la demo es 5.000 participaciones por experimento.')
    if cost > budget:
        raise ValueError(f'El carrito cuesta {cost} créditos y el límite es {budget}.')
    return {
        'id': uuid.uuid4().hex, 'label': label.strip(), 'budget': budget,
        'lines': lines, 'cost': cost, 'interval_days': interval,
        'status': 'draft', 'created_at': clock.isoformat(), 'offset_days': 0,
        'draws': [{'index': i + 1,
                   'draw_at': (clock + DAY * (1 + i * interval)).isoformat(),
                   'check_at': (clock + DAY * (2 + i * interval)).isoformat(),
                   'result': None} for i in range(count)],
    }


def evaluate(campaign, secret, clock):
    """Set each due result once; repeated polling cannot duplicate credits."""
    changed = False
    if campaign['status'] == 'draft':
        return changed
    virtual_clock = clock + DAY * campaign['offset_days']
    for draw in campaign['draws']:
        if draw['result'] is not None or virtual_clock < datetime.fromisoformat(draw['check_at']):
            continue
        digest = hashlib.sha256(f"{secret}:{campaign['id']}:{draw['index']}".encode()).digest()
        rng = random.Random(int.from_bytes(digest, 'big'))
        numbers, extra = sorted(rng.sample(range(1, 44), 5)), rng.randint(1, 16)
        results = []
        for line in campaign['lines']:
            hits = len(set(line['numbers']) & set(numbers))
            match = line['extra'] == extra
            results.append({'hits': hits, 'extra_match': match,
                            'credits': PRIZES.get((hits, match), 0)})
        draw['result'] = {'numbers': numbers, 'extra': extra, 'lines': results,
                          'credits': sum(r['credits'] for r in results),
                          'checked_at': virtual_clock.isoformat()}
        changed = True
    if all(d['result'] is not None for d in campaign['draws']):
        campaign['status'] = 'complete'
    return changed


def public_campaign(campaign, clock):
    result = json.loads(json.dumps(campaign))
    returned = sum(d['result']['credits'] for d in result['draws'] if d['result'] is not None)
    spent = 0 if result['status'] == 'draft' else result['cost']
    result.update(returned=returned, spent=spent, balance=returned - spent,
                  remaining=result['budget'] - spent + returned,
                  virtual_now=(clock + DAY * result['offset_days']).isoformat(),
                  url=f"/seguimiento/{result['id']}")
    return result


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'state.json'
        self.mutex = threading.RLock()
        self.state = (json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists()
                      else {'version': 1, 'secret': secrets.token_hex(32), 'campaigns': {}})
        if (self.state.get('version') != 1 or not isinstance(self.state.get('campaigns'), dict)
                or not isinstance(self.state.get('secret'), str)):
            raise ValueError('Formato de estado no compatible; conserva una copia antes de migrar.')

    def save(self):
        fd, temp = tempfile.mkstemp(prefix='.state-', dir=self.directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(self.state, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    @contextmanager
    def transaction(self):
        with self.mutex:
            before = json.loads(json.dumps(self.state))
            try:
                yield
            except Exception:
                self.state = before
                raise

    def create(self, payload, clock=None):
        with self.transaction():
            campaign = make_campaign(payload, clock or now())
            self.state['campaigns'][campaign['id']] = campaign
            self.save()
            return public_campaign(campaign, clock or now())

    def tick(self, clock=None):
        with self.transaction():
            clock = clock or now()
            changed = False
            for c in self.state['campaigns'].values():
                changed = evaluate(c, self.state['secret'], clock) or changed
            if changed:
                self.save()

    def action(self, key, action, clock=None):
        with self.transaction():
            clock = clock or now()
            c = self.state['campaigns'][key]
            if action == 'confirm':
                if c['status'] == 'draft':
                    c['status'] = 'confirmed'
            elif action == 'advance':
                if c['offset_days'] >= 4000:
                    raise ValueError('Se alcanzó el límite del reloj de demostración.')
                c['offset_days'] += 1
            else:
                raise ValueError('Acción desconocida.')
            evaluate(c, self.state['secret'], clock)
            self.save()
            return public_campaign(c, clock)

    def get(self, key):
        with self.mutex:
            return public_campaign(self.state['campaigns'][key], now())

    def list(self):
        with self.mutex:
            return [{'id': c['id'], 'label': c['label'], 'status': c['status'],
                     'url': f"/seguimiento/{c['id']}"}
                    for c in reversed(list(self.state['campaigns'].values()))]


@contextmanager
def process_lock(directory):
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = directory / '.server.lock'
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError(f'Otro proceso o un bloqueo previo usa {directory}. Consulta references/uso.md.')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def make_handler(store, token):
    page = (Path(__file__).resolve().parent / 'web.html').read_text(encoding='utf-8')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Avoid logging submitted numbers or experiment IDs.

        def respond(self, code, value, content_type='application/json; charset=utf-8'):
            data = (json.dumps(value, ensure_ascii=False).encode() if content_type.startswith('application/json')
                    else value.encode())
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy',
                             "default-src 'none'; script-src 'nonce-" + token + "'; "
                             "style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; "
                             "frame-ancestors 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(data)

        def host_ok(self):
            port = self.server.server_port
            return self.headers.get('Host') in (f'127.0.0.1:{port}', f'localhost:{port}')

        def do_GET(self):
            if not self.host_ok():
                return self.respond(403, {'error': 'Host no autorizado.'})
            path = urlsplit(self.path).path
            if path == '/api/health':
                return self.respond(200, {'ok': True, 'mode': 'simulation'})
            if path == '/api/campaigns':
                return self.respond(200, store.list())
            match = re.fullmatch(r'/api/campaigns/([a-f0-9]{32})', path)
            if match:
                try:
                    return self.respond(200, store.get(match[1]))
                except KeyError:
                    return self.respond(404, {'error': 'Experimento no encontrado.'})
            if path == '/' or re.fullmatch(r'/seguimiento/[a-f0-9]{32}', path):
                return self.respond(200, page.replace('__TOKEN__', token), 'text/html; charset=utf-8')
            return self.respond(404, {'error': 'Ruta no encontrada.'})

        def do_POST(self):
            if not self.host_ok() or not secrets.compare_digest(self.headers.get('X-Demo-Token', ''), token):
                return self.respond(403, {'error': 'Abre el formulario local para iniciar la demo.'})
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}',
                                         f'http://localhost:{self.server.server_port}'):
                return self.respond(403, {'error': 'Origen no autorizado.'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 1 <= length <= 65536:
                    raise ValueError('Tamaño de solicitud inválido.')
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('Se requiere JSON.')
                payload = json.loads(self.rfile.read(length))
                path = urlsplit(self.path).path
                if path == '/api/campaigns':
                    return self.respond(201, store.create(payload))
                match = re.fullmatch(r'/api/campaigns/([a-f0-9]{32})/(confirm|advance)', path)
                if match:
                    return self.respond(200, store.action(match[1], match[2]))
                return self.respond(404, {'error': 'Ruta no encontrada.'})
            except (ValueError, TypeError, UnicodeDecodeError) as error:
                return self.respond(400, {'error': str(error)})
            except KeyError:
                return self.respond(404, {'error': 'Experimento no encontrado.'})
            except OSError:
                return self.respond(500, {'error': 'No se pudo guardar el estado; revisa el almacenamiento.'})

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path.home() / '.local/share/baloto-simulador')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--poll-seconds', type=int, default=30)
    parser.add_argument('--prepare', type=Path, help='JSON de configuración; prepara un carrito y arranca.')
    args = parser.parse_args(argv)
    integer(args.port, 'Puerto', 1024, 65535)
    integer(args.poll_seconds, 'Intervalo de consulta', 1, 3600)
    payload = json.loads(args.prepare.read_text(encoding='utf-8')) if args.prepare else None
    if payload is not None:
        make_campaign(payload, now())  # Validate before creating files or binding.
    with process_lock(args.data_dir):
        store = Store(args.data_dir)
        token = secrets.token_hex(24)
        with ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(store, token)) as server:
            server.daemon_threads = True
            campaign = store.create(payload) if payload is not None else None
            store.tick()
            stop = threading.Event()

            def worker():
                while not stop.wait(args.poll_seconds):
                    try:
                        store.tick()
                    except Exception as error:
                        print(f'Actualización pendiente: {type(error).__name__}. Revisa el estado local.',
                              file=sys.stderr, flush=True)

            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            address = f'http://127.0.0.1:{args.port}'
            print(f'SIMULACIÓN — créditos ficticios, sin pagos ni conexión a Baloto.\n{address}', flush=True)
            if campaign:
                print(address + campaign['url'], flush=True)
            print(f'Datos: {store.directory}\nCtrl+C para detener.', flush=True)

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
    except (ValueError, OSError) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
