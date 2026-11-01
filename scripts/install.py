#!/usr/bin/env python3
"""Instala la skill estándar 'baloto' en Antigravity, Claude Code y/o Codex."""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, 'reconfigure', None)
        if callable(reconf):
            try:
                reconf(encoding='utf-8', errors='replace')
            except (OSError, ValueError):
                pass


def _load_baloto(repo_root: Path):
    path = repo_root / 'skills/baloto/scripts/baloto.py'
    spec = importlib.util.spec_from_file_location('baloto_install_core', path)
    if spec is None or spec.loader is None:
        raise ImportError(f'No se pudo cargar {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rmtree_safe(path: Path) -> None:
    def _handle_readonly(func, target_path, _exc):
        os.chmod(target_path, stat.S_IWRITE | stat.S_IREAD)
        func(target_path)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_handle_readonly)
    else:  # pragma: no cover - Python 3.10/3.11
        shutil.rmtree(path, onerror=_handle_readonly)


def _validate_home_root(raw_home: Path, parser: argparse.ArgumentParser) -> Path:
    expanded = Path(raw_home).expanduser()
    if expanded.is_symlink():
        parser.error(f'El directorio --home no puede ser un enlace simbólico: {expanded}')
    if not expanded.exists() or not expanded.is_dir():
        parser.error(f'El directorio --home no existe o no es una carpeta válida: {expanded}')
    home_root = expanded.resolve(strict=True)
    if home_root == Path(home_root.anchor):
        parser.error(f'Se rechaza usar la raíz del sistema como --home: {home_root}')
    return home_root


def _validate_target_path(home_root: Path, rel_dest: str, force: bool, parser: argparse.ArgumentParser) -> Path:
    parts = Path(rel_dest).parts
    if len(parts) < 2:
        parser.error(f'Ruta de instalación demasiado superficial: {rel_dest}')

    cursor = home_root
    for segment in parts[:-1]:
        cursor = cursor / segment
        if cursor.is_symlink():
            parser.error(f'Se detectó un enlace simbólico inesperado en la ruta padre: {cursor}')
        if cursor.exists() and not cursor.is_dir():
            parser.error(f' Un componente padre no es un directorio: {cursor}')

    target = home_root / rel_dest
    if target.is_symlink():
        parser.error(f'No se permite instalar ni eliminar un enlace simbólico en destino: {target}')

    if target.exists():
        if not force:
            parser.error(f'Ya existe {target}. Usa --force para actualizar la instalación existente.')
        if not target.is_dir():
            parser.error(f'El destino existente no es un directorio de skill válido: {target}')
        real_target = target.resolve(strict=True)
        if not real_target.is_relative_to(home_root) or len(real_target.relative_to(home_root).parts) < 2:
            parser.error(f'El destino resuelto escapa de --home: {real_target}')
        skill_md = real_target / 'SKILL.md'
        if not skill_md.is_file() or skill_md.is_symlink():
            parser.error(f'Se rechaza sobrescribir {target}: falta SKILL.md de propiedad verificable.')
        header_text = skill_md.read_text(encoding='utf-8', errors='replace')[:1024]
        if 'name: baloto' not in header_text:
            parser.error(f'Se rechaza sobrescribir {target}: SKILL.md no pertenece a la skill baloto.')
    return target


def _merge_installed_history(baloto_mod, source_csv: Path, existing_csv: Path, staged_csv: Path) -> None:
    if not existing_csv.is_file() or existing_csv.is_symlink():
        return
    try:
        src_rows = baloto_mod.load_history(source_csv)
        old_rows = baloto_mod.load_history(existing_csv)
    except ValueError:
        return
    by_sorteo = {r['sorteo']: r for r in src_rows}
    # El paquete fuente es la referencia verificada (corrige erratas y elimina números inexistentes);
    # solo se conservan los sorteos sincronizados localmente que sean más recientes que el paquete.
    newest_src = max(by_sorteo) if by_sorteo else 0
    for r in old_rows:
        if r['sorteo'] > newest_src:
            by_sorteo[r['sorteo']] = r
    if len(by_sorteo) > len(src_rows):
        baloto_mod.save_history(list(by_sorteo.values()), staged_csv)


def _install_atomically(baloto_mod, source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix='.baloto-stage-', dir=target.parent))
    staged_pkg = staging_root / 'baloto'
    backup_path = target.parent / f'.baloto-backup-{os.getpid()}'
    try:
        shutil.copytree(
            source,
            staged_pkg,
            symlinks=False,
            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.*'),
        )
        if target.exists():
            _merge_installed_history(
                baloto_mod,
                source / 'data/historial_baloto.csv',
                target / 'data/historial_baloto.csv',
                staged_pkg / 'data/historial_baloto.csv',
            )
            if backup_path.exists():
                _rmtree_safe(backup_path)
            os.replace(target, backup_path)
        try:
            os.replace(staged_pkg, target)
        except Exception:
            if backup_path.exists() and not target.exists():
                os.replace(backup_path, target)
            raise
        if backup_path.exists():
            _rmtree_safe(backup_path)
    finally:
        if staging_root.exists():
            _rmtree_safe(staging_root)


def main(argv=None) -> None:
    _reconfigure_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--agent',
        choices=['antigravity', 'claude', 'codex', 'all'],
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
        help='Actualiza/reemplaza una instalación previa verificada de la skill si ya existe.',
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / 'skills/baloto'
    if not source.is_dir():
        parser.error(f'No se encontró el paquete fuente en {source}')

    baloto_mod = _load_baloto(repo_root)
    home_root = _validate_home_root(args.home, parser)

    rel_destinations = {
        'antigravity': '.gemini/config/skills/baloto',
        'claude': '.claude/skills/baloto',
        'codex': '.agents/skills/baloto',
    }
    selected_keys = list(rel_destinations) if args.agent == 'all' else [args.agent]
    targets = [_validate_target_path(home_root, rel_destinations[k], args.force, parser) for k in selected_keys]

    for target in targets:
        _install_atomically(baloto_mod, source, target)
        print(f'Instalada: {target}')


if __name__ == '__main__':
    try:
        main()
    except OSError as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
