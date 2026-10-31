#!/usr/bin/env python3
"""Copy the portable skill to the selected agent directories; never overwrite."""
import argparse
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', choices=['codex', 'claude', 'both'], default='both')
    parser.add_argument('--home', type=Path, default=Path.home(), help='Alternative home for isolated testing')
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / 'skills/baloto-simulador'
    destinations = {'codex': args.home / '.agents/skills/baloto-simulador',
                    'claude': args.home / '.claude/skills/baloto-simulador'}
    selected = list(destinations.values()) if args.agent == 'both' else [destinations[args.agent]]
    for target in selected:
        if target.exists() or target.is_symlink():
            parser.error(f'Ya existe {target}. No se sobrescribió ninguna instalación.')
    for target in selected:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        print(f'Instalada: {target}')


if __name__ == '__main__':
    main()
