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
PLAY_URL = 'https://baloto.com/'
STOP_BEFORE_CHECKOUT = True


def _load_baloto():
    path = Path(__file__).resolve().parent / 'baloto.py'
    spec = importlib.util.spec_from_file_location('baloto_core', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


baloto = _load_baloto()


def _group_into_slips(lines: list[dict], draw_count: int, price_per_line: int, mode: str) -> list[dict]:
    """Agrupa líneas en boletas de máximo 5 paneles (A–E), respetando combos si existen."""
    slips: list[dict] = []
    buckets: list[tuple[str | None, list[dict]]] = []

    for line in lines:
        combo = line.get('combo')
        if buckets and buckets[-1][0] == combo and len(buckets[-1][1]) < 5:
            buckets[-1][1].append(line)
        else:
            buckets.append((combo, [line]))

    for slip_idx, (combo, group) in enumerate(buckets, 1):
        panels = []
        for panel_letter, line in zip(PANEL_LETTERS, group):
            panels.append({
                'panel': panel_letter,
                'numbers': line['numbers'],
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
            'slip_cost_formatted': baloto.fmt_cop(slip_cost),
        })
    return slips


def build_cart_plan(source: dict) -> dict:
    if 'draws' in source and 'price_per_line' in source and isinstance(source.get('lines'), list):
        ticket = source
    else:
        ticket = baloto.make_ticket(source, datetime.now(timezone.utc))

    slips = _group_into_slips(
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
            '1. Verifica que la persona ya inició sesión en baloto.com en su propio navegador (nunca pidas ni ingreses credenciales).',
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


def generate_browser_snippet(plan: dict) -> str:
    """Genera un asistente JS auditable para inyectar vía DevTools/MCP en la página de juego."""
    safe_payload = {
        'label': plan['label'],
        'mode': plan['mode'],
        'mode_label': plan['mode_label'],
        'draw_count': plan['draw_count'],
        'total_cost_formatted': plan['total_cost_formatted'],
        'slips': plan['slips'],
    }
    encoded = json.dumps(safe_payload, ensure_ascii=False, indent=2)
    return f"""(() => {{
  const STOP_BEFORE_CHECKOUT = true;
  const PLAN = {encoded};

  // Nunca interactúa con formularios de login, datos personales ni pasarelas de pago.
  const FORBIDDEN_RE = /(pagar|pago|pse|checkout|tarjeta|clave|contrase|login|iniciar sesi)/i;

  function renderGuideOverlay() {{
    let box = document.getElementById('baloto-skill-helper');
    if (!box) {{
      box = document.createElement('aside');
      box.id = 'baloto-skill-helper';
      box.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:999999;max-width:360px;max-height:75vh;overflow:auto;background:#132334;color:#f5f7f9;padding:16px;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.35);font:13px/1.45 system-ui,sans-serif';
      document.body.appendChild(box);
    }}
    const items = PLAN.slips.map(s => {{
      const rows = s.panels.map(p =>
        `<div><b>${{p.panel}}:</b> ${{p.numbers_formatted.join('·')}} + <span style="color:#ffd166">${{p.superbalota_formatted}}</span></div>`
      ).join('');
      return `<div style="margin:8px 0;padding:8px;background:#1d3247;border-radius:8px"><strong>Boleta #${{s.slip_index}} (${{s.combo}})</strong> · ${{s.slip_cost_formatted}}${{rows}}</div>`;
    }}).join('');
    box.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><strong>Guía carrito Baloto</strong><button id="baloto-skill-close" style="background:transparent;border:1px solid #5c738a;color:#fff;border-radius:6px;padding:2px 7px;cursor:pointer">×</button></div>
      <div style="color:#b8c7d6;margin-top:4px">${{PLAN.mode_label}} · ${{PLAN.draw_count}} sorteo(s) · Total: <b>${{PLAN.total_cost_formatted}}</b></div>
      ${{items}}
      <div style="color:#a8e6cf;font-size:12px;margin-top:8px">Verifica el total en el carrito y realiza el pago manualmente.</div>`;
    document.getElementById('baloto-skill-close').onclick = () => box.remove();
    return {{ ok: true, stopBeforeCheckout: STOP_BEFORE_CHECKOUT, slips: PLAN.slips.length, total: PLAN.total_cost_formatted }};
  }}

  window.__balotoCartPlan = PLAN;
  window.__balotoForbiddenTarget = el => FORBIDDEN_RE.test((el?.innerText || el?.value || el?.id || ''));
  return renderGuideOverlay();
}})();
"""


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--input', type=Path, help='Ruta a un archivo JSON con la configuración de la jugada.')
    group.add_argument('--ticket-id', help='ID de un tiquete guardado en el directorio local.')
    parser.add_argument('--data-dir', type=Path, default=Path.home() / '.local/share/baloto')
    parser.add_argument('--mode', choices=['plan', 'js', 'json'], default='plan',
                        help='Formato de salida: guía legible (plan), asistente JS para navegador (js) o JSON.')
    args = parser.parse_args(argv)

    if args.input:
        source = json.loads(args.input.read_text(encoding='utf-8'))
    else:
        store = baloto.Store(args.data_dir)
        source = store.get(args.ticket_id)

    plan = build_cart_plan(source)
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
