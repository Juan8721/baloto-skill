#!/usr/bin/env python3
"""Instala la skill estándar 'baloto' en Antigravity, Claude Code y/o Codex."""
import argparse
from pathlib import Path
import shutil


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--agent',
        choices=['antigravity', 'claude', 'codex', 'both', 'all'],
        default='all',
        help="Destino de instalación: 'antigravity', 'claude', 'codex' o 'all' (por defecto).",
    )
    parser.add_argument(
        '--home',
        type=Path,
        default=Path.home(),
        help='Directorio home alternativo para pruebas aisladas.',
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Actualiza/reemplaza una instalación previa de la skill si ya existe.',
    )
    args = parser.parse_args(argv)

    source = Path(__file__).resolve().parents[1] / 'skills/baloto'
    if not source.is_dir():
        parser.error(f'No se encontró el paquete fuente en {source}')

    destinations = {
        'antigravity': args.home / '.gemini/config/skills/baloto',
        'claude': args.home / '.claude/skills/baloto',
        'codex': args.home / '.agents/skills/baloto',
    }
    if args.agent == 'all':
        selected = list(destinations.values())
    elif args.agent == 'both':
        selected = [destinations['codex'], destinations['claude']]
    else:
        selected = [destinations[args.agent]]

    if not args.force:
        for target in selected:
            if target.exists() or target.is_symlink():
                parser.error(
                    f'Ya existe {target}. Usa --force para actualizar la instalación existente.'
                )

    for target in selected:
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        print(f'Instalada: {target}')


if __name__ == '__main__':
    main()
