#!/usr/bin/env python3
"""Helper determinista para preparar jugadas en el carrito de baloto.com sin ejecutar pagos."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys

PANEL_LETTERS = ('A', 'B', 'C', 'D', 'E')
PLAY_URL = 'https://apuestaaqui.baloto.com/'
STOP_BEFORE_CHECKOUT = True


def _load_baloto():
    path = Path(__file__).resolve().parent / 'baloto.py'
    spec = importlib.util.spec_from_file_location('baloto_core', path)
    if spec is None or spec.loader is None:
        raise ImportError(f'No se pudo cargar {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


baloto = _load_baloto()


def _serialize_lines_list(raw_lines: list) -> str:
    if not isinstance(raw_lines, list) or not 1 <= len(raw_lines) <= 500:
        raise ValueError('Introduce entre 1 y 500 líneas por tiquete.')
    out_lines: list[str] = []
    for idx, item in enumerate(raw_lines, 1):
        if not isinstance(item, dict):
            raise ValueError(f'Línea {idx}: formato de línea inválido.')
        nums = item.get('numbers')
        extra = item.get('extra')
        if not isinstance(nums, list) or len(nums) != 5 or not all(type(n) is int for n in nums):
            raise ValueError(f'Línea {idx}: se necesitan cinco números enteros.')
        if type(extra) is not int:
            raise ValueError(f'Línea {idx}: Superbalota inválida.')
        body = f"{' '.join(f'{n:02d}' for n in nums)} | {extra:02d}"
        combo = item.get('combo')
        if combo is not None:
            if not isinstance(combo, str) or ':' in combo:
                raise ValueError(f'Línea {idx}: etiqueta de combo inválida.')
            out_lines.append(f'{combo}: {body}')
        else:
            out_lines.append(body)
    return '\n'.join(out_lines)


def _normalize_ticket(source: dict, default_start_draw: int | None = None) -> dict:
    if not isinstance(source, dict):
        raise ValueError('La configuración de la jugada debe ser un objeto JSON.')

    if isinstance(source.get('lines'), list):
        clean_payload: dict = {
            'label': source.get('label', 'Mi tiquete Baloto'),
            'mode': source.get('mode', 'baloto_revancha'),
            'draw_count': source.get('draw_count', 1),
            'lines': _serialize_lines_list(source['lines']),
        }
        if source.get('start_draw') is not None:
            clean_payload['start_draw'] = source['start_draw']
        if source.get('price_per_line') is not None:
            clean_payload['price_per_line'] = source['price_per_line']
        return baloto.make_ticket(
            clean_payload,
            datetime.now(timezone.utc),
            default_start_draw=default_start_draw,
        )

    return baloto.make_ticket(
        source,
        datetime.now(timezone.utc),
        default_start_draw=default_start_draw,
    )


def build_cart_plan(source: dict, default_start_draw: int | None = None) -> dict:
    ticket = _normalize_ticket(source, default_start_draw=default_start_draw)
    slips = baloto.group_into_slips(
        ticket['lines'],
        draw_count=ticket['draw_count'],
        price_per_line=ticket['price_per_line'],
        mode=ticket['mode'],
    )
    mode_label = 'Baloto + Revancha' if ticket['mode'] == 'baloto_revancha' else 'Solo Baloto'
    end_draw = ticket['start_draw'] + ticket['draw_count'] - 1

    return {
        'label': ticket['label'],
        'play_url': PLAY_URL,
        'mode': ticket['mode'],
        'mode_label': mode_label,
        'start_draw': ticket['start_draw'],
        'end_draw': end_draw,
        'draw_count': ticket['draw_count'],
        'price_per_line_cop': ticket['price_per_line'],
        'total_lines': len(ticket['lines']),
        'total_cost_cop': ticket['cost'],
        'total_cost_formatted': baloto.fmt_cop(ticket['cost']),
        'stop_before_payment': STOP_BEFORE_CHECKOUT,
        'slips': slips,
        'agent_steps': [
            f'1. Verifica que la persona ya inició sesión en {PLAY_URL} (portal oficial "Juega en línea" de baloto.com) en su propio navegador; nunca pidas ni ingreses credenciales.',
            '2. Entra a la sección de juego manual de Baloto y configura la cantidad de sorteos consecutivos indicada.',
            f"3. Carga {len(slips)} boleta(s) de hasta 5 paneles (A–E) en modalidad {mode_label}.",
            f"4. Verifica en el resumen del carrito que el total coincida con {baloto.fmt_cop(ticket['cost'])} ({len(ticket['lines'])} líneas × {ticket['draw_count']} sorteo(s)).",
            '5. DETENTE en el resumen del carrito. La revisión final y el pago los ejecuta siempre el usuario.',
        ],
    }


def format_plan_markdown(plan: dict) -> str:
    out = [
        f"# Plan de carrito Baloto: {plan['label']}",
        '',
        f"- **Portal de juego:** {plan['play_url']}",
        f"- **Modalidad:** {plan['mode_label']} ({baloto.fmt_cop(plan['price_per_line_cop'])} por línea/sorteo)",
        f"- **Sorteos:** {plan['draw_count']} consecutivo(s) (#{plan['start_draw']} → #{plan['end_draw']})",
        f"- **Total líneas:** {plan['total_lines']} en {len(plan['slips'])} boleta(s) (máx. 5 paneles A–E por boleta)",
        f"- **Total esperado en carrito:** **{plan['total_cost_formatted']} COP**",
        '- **Límite de seguridad:** Detenerse siempre en el carrito; el usuario inicia sesión y realiza el pago.',
        '',
        '## Boletas por cargar',
    ]
    for slip in plan['slips']:
        out.append(f"\n### Boleta #{slip['slip_index']} ({slip['combo']}) — {slip['slip_cost_formatted']}")
        for p in slip['panels']:
            nums = ' - '.join(p['numbers_formatted'])
            rev = 'Baloto + Revancha' if p['revancha'] else 'Solo Baloto'
            out.append(f"- **Panel {p['panel']}:** `{nums}` | SB `{p['superbalota_formatted']}` ({rev})")
    out.append('\n## Pasos para el navegador del agente')
    out.extend(plan['agent_steps'])
    return '\n'.join(out) + '\n'


def _safe_json_for_js(value: dict) -> str:
    return (
        json.dumps(value, ensure_ascii=False, indent=2)
        .replace('</', r'<\/')
        .replace('\u2028', r'\u2028')
        .replace('\u2029', r'\u2029')
    )


def generate_browser_snippet(plan: dict) -> str:
    """Genera un asistente JS auditable sin innerHTML para inyectar vía DevTools/MCP en la página de juego."""
    safe_payload = {
        'label': str(plan['label']),
        'mode': str(plan['mode']),
        'mode_label': str(plan['mode_label']),
        'draw_count': int(plan['draw_count']),
        'total_cost_formatted': str(plan['total_cost_formatted']),
        'slips': plan['slips'],
    }
    encoded = _safe_json_for_js(safe_payload)
    return f"""(() => {{
  const STOP_BEFORE_CHECKOUT = true;
  const PLAN = {encoded};

  // Nunca interactúa con formularios de autenticación, datos personales ni pasarelas de pago.
  const FORBIDDEN_RE = /(pagar|pago|pse|checkout|comprar|finalizar|tarjeta|cvv|clave|contrase|pass|login|autentic|iniciar sesi)/i;

  function inspectNodeText(node) {{
    if (!node || typeof node !== 'object') return '';
    const aria = typeof node.getAttribute === 'function' ? (node.getAttribute('aria-label') || '') : '';
    const cls = typeof node.className === 'string' ? node.className : '';
    return [
      node.textContent || '',
      node.innerText || '',
      node.value || '',
      node.id || '',
      node.name || '',
      node.type || '',
      node.href || '',
      node.formAction || '',
      node.title || '',
      aria,
      cls,
    ].join(' ');
  }}

  function isForbiddenTarget(el) {{
    if (!el) return false;
    const interactive = typeof el.closest === 'function'
      ? el.closest('button,a,input,select,textarea,form,[role="button"]')
      : null;
    return [el, interactive].some(candidate => candidate && FORBIDDEN_RE.test(inspectNodeText(candidate)));
  }}

  function makeEl(tag, style, text) {{
    const el = document.createElement(tag);
    if (style) el.style.cssText = style;
    if (text !== undefined && text !== null) el.textContent = String(text);
    return el;
  }}

  function renderGuideOverlay() {{
    let box = document.getElementById('baloto-skill-helper');
    if (!box) {{
      box = makeEl(
        'aside',
        'position:fixed;bottom:16px;right:16px;z-index:999999;max-width:360px;max-height:75vh;overflow:auto;background:#132334;color:#f5f7f9;padding:16px;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.35);font:13px/1.45 system-ui,sans-serif'
      );
      box.id = 'baloto-skill-helper';
      box.setAttribute('role', 'region');
      box.setAttribute('aria-label', 'Guía carrito Baloto');
      document.body.appendChild(box);
    }}

    const header = makeEl('div', 'display:flex;justify-content:space-between;align-items:center;gap:8px');
    header.appendChild(makeEl('strong', '', 'Guía carrito Baloto — ' + PLAN.label));
    const closeBtn = makeEl(
      'button',
      'background:transparent;border:1px solid #8fa4b8;color:#fff;border-radius:6px;padding:2px 8px;cursor:pointer',
      '×'
    );
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', 'Cerrar guía de carrito Baloto');
    closeBtn.addEventListener('click', () => box.remove());
    header.appendChild(closeBtn);

    const summary = makeEl('div', 'color:#d0dce8;margin-top:4px');
    summary.appendChild(document.createTextNode(PLAN.mode_label + ' · ' + PLAN.draw_count + ' sorteo(s) · Total: '));
    summary.appendChild(makeEl('strong', '', PLAN.total_cost_formatted));

    const listWrap = makeEl('div', '');
    for (const s of PLAN.slips) {{
      const card = makeEl('div', 'margin:8px 0;padding:8px;background:#1d3247;border-radius:8px');
      const cardHead = makeEl('div', '');
      cardHead.appendChild(makeEl('strong', '', 'Boleta #' + s.slip_index + ' (' + s.combo + ')'));
      cardHead.appendChild(document.createTextNode(' · ' + s.slip_cost_formatted));
      card.appendChild(cardHead);

      for (const p of s.panels) {{
        const row = makeEl('div', 'margin-top:2px');
        row.appendChild(makeEl('strong', '', p.panel + ': '));
        row.appendChild(document.createTextNode(p.numbers_formatted.join('·') + ' + '));
        row.appendChild(makeEl('span', 'color:#ffd166;font-weight:700', p.superbalota_formatted));
        card.appendChild(row);
      }}
      listWrap.appendChild(card);
    }}

    const footer = makeEl(
      'div',
      'color:#a8e6cf;font-size:12px;margin-top:8px',
      'Verifica el total en el carrito y realiza el pago manualmente.'
    );

    box.replaceChildren(header, summary, listWrap, footer);
    return {{ ok: true, stopBeforeCheckout: STOP_BEFORE_CHECKOUT, slips: PLAN.slips.length, total: PLAN.total_cost_formatted }};
  }}

  window.__balotoCartPlan = PLAN;
  window.__balotoForbiddenTarget = isForbiddenTarget;
  return renderGuideOverlay();
}})();
"""


def main(argv=None) -> None:
    baloto.reconfigure_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--input', type=str, metavar='ARCHIVO_O_GUION',
                       help="Ruta JSON o '-' (stdin) con la configuración de la jugada.")
    group.add_argument('--ticket-id', help='ID de un tiquete guardado en el directorio local.')
    parser.add_argument('--data-dir', type=Path, default=baloto.default_data_dir())
    parser.add_argument('--history', type=Path, default=baloto.DEFAULT_HISTORY_PATH)
    parser.add_argument('--mode', choices=['plan', 'js', 'json'], default='plan',
                        help='Formato de salida: guía legible (plan), asistente JS para navegador (js) o JSON.')
    args = parser.parse_args(argv)

    default_next = baloto._default_next_draw(args.history)
    if args.input:
        raw_text = sys.stdin.read() if args.input == '-' else Path(args.input).expanduser().read_text(encoding='utf-8')
        source = json.loads(raw_text)
    else:
        store = baloto.Store(args.data_dir, history_path=args.history)
        source = store.get(args.ticket_id)

    plan = build_cart_plan(source, default_start_draw=default_next)
    if args.mode == 'json':
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    elif args.mode == 'js':
        print(generate_browser_snippet(plan))
    else:
        print(format_plan_markdown(plan), end='')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
