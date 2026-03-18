"""
scfs — command-line interface for sidecarFS.

All destructive operations move files to a trash folder instead of deleting
them.  The default trash location is ~/.sidecarfs_trash/ and can be overridden
with the --trash-root option or the SIDECARFS_TRASH env variable.
"""

import os
import sys
from pathlib import Path
from typing import Optional

import click

from .core import SidecarFS, SIDECAR_EXTENSIONS

_DEFAULT_TRASH = Path.home() / ".sidecarfs_trash"


def _get_trash_root(trash_root: Optional[str]) -> Path:
    if trash_root:
        return Path(trash_root)
    env = os.environ.get("SIDECARFS_TRASH")
    if env:
        return Path(env)
    return _DEFAULT_TRASH


def _is_sidecar(path: Path) -> bool:
    return path.suffix.lower() in SIDECAR_EXTENSIONS


def _primary_candidates(directory: Path):
    """Yield files that are not themselves sidecars of another file in the dir."""
    all_files = sorted(f for f in directory.iterdir() if f.is_file())
    sidecar_stems = set()
    for f in all_files:
        if _is_sidecar(f):
            # This could be a sidecar of another file — mark its stem
            sidecar_stems.add(f.stem)

    for f in all_files:
        # A file is primary if it is not a sidecar extension, OR if its stem
        # has no corresponding primary (i.e., it's a lone .md / .json etc.)
        if not _is_sidecar(f):
            yield f, True   # definitely primary
        elif f.stem not in {g.stem for g in all_files if not _is_sidecar(g)}:
            yield f, False  # sidecar extension but no primary sibling → show it


@click.group()
def cli():
    """scfs — safe filesystem operations with sidecar awareness."""


@cli.command("ls")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("-a", "--all", "show_all", is_flag=True, help="Include hidden files")
def cmd_ls(path: str, show_all: bool):
    """List files, annotating those that have sidecars.

    Sidecar files themselves are not listed separately — they appear as
    annotations on the primary file line.

    \b
    Example:
      sfs ls "/Volumes/Media/TV Shows/"
      sfs ls .
    """
    directory = Path(path)

    if directory.is_file():
        # Single file mode
        fs = SidecarFS()
        group = fs.find_group(directory)
        sc_note = ""
        if group.sidecars:
            exts = " ".join(s.suffix for s in group.sidecars)
            sc_note = f"  [sidecars: {exts}]"
        click.echo(f"{directory.name}{sc_note}")
        return

    fs = SidecarFS()

    entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    shown = 0
    for entry in entries:
        if not show_all and entry.name.startswith("."):
            continue

        if entry.is_dir():
            click.echo(f"{entry.name}/")
            shown += 1
            continue

        if _is_sidecar(entry):
            # Check if a primary sibling exists — if so, skip (it will be annotated)
            has_primary = any(
                sib.stem == entry.stem and not _is_sidecar(sib)
                for sib in directory.iterdir()
                if sib.is_file()
            )
            if has_primary:
                continue  # suppress — shown as annotation on primary

        group = fs.find_group(entry)
        if group.sidecars:
            exts = " ".join(s.suffix for s in group.sidecars)
            click.echo(f"{entry.name}  [sidecars: {exts}]")
        else:
            click.echo(entry.name)
        shown += 1

    if shown == 0:
        click.echo("(empty)")


@cli.command("mv")
@click.argument("source", type=click.Path(exists=True))
@click.argument("target")
@click.option("-n", "--dry-run", is_flag=True, help="Show what would be moved without moving")
def cmd_mv(source: str, target: str, dry_run: bool):
    """Move SOURCE and its sidecars to TARGET directory.

    TARGET must be a directory (existing or to be created).

    \b
    Example:
      sfs mv episode.mp4 ../Season2/
      sfs mv episode.mp4 ../Season2/ --dry-run
    """
    source_path = Path(source)
    target_path = Path(target)

    if target_path.exists() and not target_path.is_dir():
        click.echo(f"error: target '{target}' exists and is not a directory", err=True)
        sys.exit(1)

    fs = SidecarFS(dry_run=dry_run)
    group = fs.find_group(source_path)

    sc_desc = f" + {len(group.sidecars)} sidecar(s)" if group.sidecars else ""
    verb = "Would move" if dry_run else "Moving"
    click.echo(f"{verb}: {source_path.name}{sc_desc}  →  {target_path}/")

    result = fs.move(source_path, target_path)
    if result is None and not dry_run:
        click.echo(f"error: move failed", err=True)
        sys.exit(1)


@cli.command("cp")
@click.argument("source", type=click.Path(exists=True))
@click.argument("target")
@click.option("-n", "--dry-run", is_flag=True, help="Show what would be copied without copying")
def cmd_cp(source: str, target: str, dry_run: bool):
    """Copy SOURCE and its sidecars to TARGET directory.

    TARGET must be a directory (existing or to be created).

    \b
    Example:
      sfs cp episode.mp4 /backup/Season2/
      sfs cp episode.mp4 /backup/Season2/ --dry-run
    """
    source_path = Path(source)
    target_path = Path(target)

    if target_path.exists() and not target_path.is_dir():
        click.echo(f"error: target '{target}' exists and is not a directory", err=True)
        sys.exit(1)

    fs = SidecarFS(dry_run=dry_run)
    group = fs.find_group(source_path)

    sc_desc = f" + {len(group.sidecars)} sidecar(s)" if group.sidecars else ""
    verb = "Would copy" if dry_run else "Copying"
    click.echo(f"{verb}: {source_path.name}{sc_desc}  →  {target_path}/")

    result = fs.copy(source_path, target_path)
    if result is None and not dry_run:
        click.echo(f"error: copy failed", err=True)
        sys.exit(1)


@cli.command("rm")
@click.argument("sources", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--trash-root", default=None, help="Trash folder (default: ~/.sidecarfs_trash)")
@click.option("-n", "--dry-run", is_flag=True, help="Show what would be trashed without moving")
def cmd_rm(sources: tuple, trash_root: Optional[str], dry_run: bool):
    """Safely remove files by moving them (and sidecars) to trash.

    Files are NEVER permanently deleted — they go to the trash folder and
    can be recovered manually.

    Default trash location: ~/.sidecarfs_trash/
    Override: --trash-root /path/to/trash  or  SIDECARFS_TRASH env variable.

    \b
    Example:
      sfs rm episode.mp4
      sfs rm *.mp4
      sfs rm episode.mp4 --trash-root /Volumes/Media/_trash
      sfs rm episode.mp4 --dry-run
    """
    resolved_trash = _get_trash_root(trash_root)
    fs = SidecarFS(trash_root=resolved_trash, dry_run=dry_run)

    for source in sources:
        source_path = Path(source)
        group = fs.find_group(source_path)

        sc_desc = f" + {len(group.sidecars)} sidecar(s)" if group.sidecars else ""
        verb = "Would trash" if dry_run else "Trashing"
        click.echo(f"{verb}: {source_path.name}{sc_desc}  →  {resolved_trash}/")

        result = fs.trash(source_path)
        if result is None and not dry_run:
            click.echo(f"error: trash failed for {source_path.name}", err=True)
            sys.exit(1)


@cli.command("ln")
@click.argument("source", type=click.Path(exists=True))
@click.argument("link_dir")
@click.option("--name", default=None, help="Override symlink name for the primary file")
@click.option("--absolute", is_flag=True, help="Use absolute symlink targets (default: relative)")
@click.option("-n", "--dry-run", is_flag=True, help="Show what would be linked without creating")
def cmd_ln(source: str, link_dir: str, name: Optional[str], absolute: bool, dry_run: bool):
    """Create symlinks for SOURCE and its sidecars in LINK_DIR.

    Creates one symlink per file in the group (primary + each sidecar) so
    that tools looking for companion files in the link directory find them
    even when the real files live in a _store/ hierarchy.

    Symlink targets are relative by default (portable across mounts).
    Use --absolute for absolute targets.

    \b
    Example:
      scfs ln _store/ARD/Tatort/ep.mp4  "TV Shows/Tatort/Season 2021/"
      scfs ln _store/ARD/Tatort/ep.mp4  "TV Shows/Tatort/Season 2021/" --name "Tatort - s2021e01 - Title.mp4"
      scfs ln _store/ARD/Tatort/ep.mp4  "TV Shows/Tatort/Season 2021/" --dry-run
    """
    source_path = Path(source)
    link_dir_path = Path(link_dir)

    fs = SidecarFS(dry_run=dry_run)
    group = fs.find_group(source_path)

    sc_desc = f" + {len(group.sidecars)} sidecar link(s)" if group.sidecars else ""
    link_name = name or source_path.name
    verb = "Would link" if dry_run else "Linking"
    click.echo(f"{verb}: {link_name}{sc_desc}  →  {link_dir_path}/")

    result = fs.link(source_path, link_dir_path, link_name=name, relative=not absolute)
    if result is None and not dry_run:
        click.echo("error: link failed", err=True)
        sys.exit(1)


def main():
    cli()
