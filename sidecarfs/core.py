"""
Core filesystem wrapper that keeps primary files and their sidecars in sync.
"""

import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Extensions that are considered sidecars of a primary file
SIDECAR_EXTENSIONS = {".properties", ".json", ".md"}


@dataclass
class FileGroup:
    """A primary file together with all its discovered sidecars."""

    primary: Path
    sidecars: List[Path] = field(default_factory=list)

    def all_files(self) -> List[Path]:
        return [self.primary] + self.sidecars

    def exists(self) -> bool:
        return self.primary.exists()

    def total_size(self) -> int:
        return sum(f.stat().st_size for f in self.all_files() if f.exists())

    def __repr__(self) -> str:
        sc = ", ".join(s.name for s in self.sidecars)
        return f"FileGroup({self.primary.name}, sidecars=[{sc}])"


class SidecarFS:
    """
    Filesystem operations that keep primary files and their sidecars together.

    Parameters
    ----------
    trash_root:
        Base directory for the trash folder.  When *trash_root* is None,
        operations that would move to trash raise ``ValueError`` unless a
        per-call override is supplied.
    dry_run:
        When True, log planned operations but do not touch the filesystem.
    """

    def __init__(
        self,
        trash_root: Optional[Path] = None,
        dry_run: bool = False,
    ):
        self.trash_root = Path(trash_root) if trash_root else None
        self.dry_run = dry_run

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def find_group(self, primary: Path) -> FileGroup:
        """Return a FileGroup for *primary* by discovering its sidecars."""
        primary = Path(primary)
        sidecars: List[Path] = []
        base = primary.stem
        parent = primary.parent

        for ext in SIDECAR_EXTENSIONS:
            candidate = parent / f"{base}{ext}"
            if candidate.exists() and candidate != primary:
                sidecars.append(candidate)

        # Also match sidecars when the primary has an edition tag, e.g.
        # "Episode {edition-1080p}.mp4" → "Episode.md"
        if "{edition-" in base:
            clean_base = base.split("{edition-")[0].rstrip()
            for ext in SIDECAR_EXTENSIONS:
                candidate = parent / f"{clean_base}{ext}"
                if candidate.exists() and candidate not in sidecars and candidate != primary:
                    sidecars.append(candidate)

        return FileGroup(primary=primary, sidecars=sidecars)

    # ------------------------------------------------------------------
    # Move
    # ------------------------------------------------------------------

    def move(self, source: Path, target_dir: Path) -> Optional[FileGroup]:
        """
        Move *source* and its sidecars into *target_dir*.

        Returns the new FileGroup on success, None on failure.
        """
        source = Path(source)
        target_dir = Path(target_dir)
        group = self.find_group(source)
        return self._move_group(group, target_dir)

    def move_group(self, group: FileGroup, target_dir: Path) -> Optional[FileGroup]:
        """Move a pre-built FileGroup into *target_dir*."""
        return self._move_group(group, Path(target_dir))

    # ------------------------------------------------------------------
    # Copy
    # ------------------------------------------------------------------

    def copy(self, source: Path, target_dir: Path) -> Optional[FileGroup]:
        """
        Copy *source* and its sidecars into *target_dir*.

        Returns the new FileGroup on success, None on failure.
        """
        source = Path(source)
        target_dir = Path(target_dir)
        group = self.find_group(source)
        return self._copy_group(group, target_dir)

    # ------------------------------------------------------------------
    # Trash (safe delete)
    # ------------------------------------------------------------------

    def trash(
        self,
        source: Path,
        trash_root: Optional[Path] = None,
    ) -> Optional[FileGroup]:
        """
        Move *source* and its sidecars to the trash folder instead of
        deleting them.

        The trash destination preserves the relative path under *trash_root*
        so items can be restored manually.  A datestamp sub-folder is used
        to prevent collisions.

        Parameters
        ----------
        source:
            Primary file to trash.
        trash_root:
            Override the instance-level trash_root for this call.

        Returns the trashed FileGroup on success, None on failure.
        """
        resolved_trash_root = trash_root or self.trash_root
        if resolved_trash_root is None:
            raise ValueError(
                "trash_root must be supplied either at construction time "
                "or as a per-call argument"
            )

        source = Path(source)
        resolved_trash_root = Path(resolved_trash_root)
        group = self.find_group(source)

        # Build a trash destination that mirrors the original relative layout.
        # Destination: <trash_root>/<YYYY-MM-DD>/<original-relative-path-parent>/
        datestamp = datetime.now().strftime("%Y-%m-%d")
        try:
            # Try to build a meaningful relative path from a common ancestor.
            rel = source.parent.relative_to(resolved_trash_root.parent)
        except ValueError:
            rel = Path(source.parent.name)
        trash_dir = resolved_trash_root / datestamp / rel

        logger.info(f"TRASH {group.primary} → {trash_dir}")
        return self._move_group(group, trash_dir)

    def trash_group(
        self,
        group: FileGroup,
        trash_root: Optional[Path] = None,
    ) -> Optional[FileGroup]:
        """Move a pre-built FileGroup to trash."""
        resolved_trash_root = trash_root or self.trash_root
        if resolved_trash_root is None:
            raise ValueError(
                "trash_root must be supplied either at construction time "
                "or as a per-call argument"
            )

        resolved_trash_root = Path(resolved_trash_root)
        datestamp = datetime.now().strftime("%Y-%m-%d")
        try:
            rel = group.primary.parent.relative_to(resolved_trash_root.parent)
        except ValueError:
            rel = Path(group.primary.parent.name)
        trash_dir = resolved_trash_root / datestamp / rel

        logger.info(f"TRASH_GROUP {group.primary} → {trash_dir}")
        return self._move_group(group, trash_dir)

    # ------------------------------------------------------------------
    # Symlink
    # ------------------------------------------------------------------

    def link(
        self,
        source: Path,
        link_dir: Path,
        link_name: Optional[str] = None,
        relative: bool = True,
    ) -> Optional["FileGroup"]:
        """
        Create symlinks in *link_dir* pointing at *source* and its sidecars.

        This is the sidecar-aware ``ln -s``: one symlink per file in the group
        so that tools looking for ``episode.md`` in the view directory find it
        even when the real file lives in a ``_store/`` hierarchy.

        Parameters
        ----------
        source:
            Primary file to link to.
        link_dir:
            Directory where symlinks will be created.
        link_name:
            Override the symlink name for the primary file.  Sidecars keep
            their original names.  If omitted, ``source.name`` is used.
        relative:
            When True (default) the symlink target is a relative path from
            *link_dir* to the real file — more portable across mounts.
            When False an absolute target path is used.

        Returns a FileGroup of the created symlink paths, or None on failure.
        """
        source = Path(source)
        link_dir = Path(link_dir)
        group = self.find_group(source)

        if self.dry_run:
            primary_link_name = link_name or source.name
            logger.info(
                f"DRY RUN link: {primary_link_name} + {len(group.sidecars)} sidecar link(s) → {link_dir}"
            )
            return group

        link_dir.mkdir(parents=True, exist_ok=True)
        created: List[Path] = []

        try:
            primary_link_name = link_name or source.name
            primary_link = link_dir / primary_link_name
            target = (
                Path(os.path.relpath(source, link_dir)) if relative else source.resolve()
            )
            if primary_link.exists() or primary_link.is_symlink():
                primary_link.unlink()
            primary_link.symlink_to(target)
            created.append(primary_link)
            logger.debug(f"Linked {primary_link} → {target}")

            sidecar_links: List[Path] = []
            for sc in group.sidecars:
                sc_link = link_dir / sc.name
                sc_target = (
                    Path(os.path.relpath(sc, link_dir)) if relative else sc.resolve()
                )
                if sc_link.exists() or sc_link.is_symlink():
                    sc_link.unlink()
                sc_link.symlink_to(sc_target)
                created.append(sc_link)
                sidecar_links.append(sc_link)
                logger.debug(f"Linked {sc_link} → {sc_target}")

            logger.info(
                f"Linked {primary_link_name} + {len(sidecar_links)} sidecar link(s) → {link_dir}"
            )
            return FileGroup(primary=primary_link, sidecars=sidecar_links)

        except Exception as exc:
            logger.error(f"Link failed for {source}: {exc}")
            for lnk in created:
                try:
                    if lnk.is_symlink():
                        lnk.unlink()
                except Exception:
                    pass
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _move_group(self, group: FileGroup, target_dir: Path) -> Optional[FileGroup]:
        """Move all files in group to target_dir, return new FileGroup."""
        if not group.exists():
            logger.warning(f"Primary file does not exist: {group.primary}")
            return None

        if self.dry_run:
            logger.info(f"DRY RUN move: {group.primary.name} + {len(group.sidecars)} sidecars → {target_dir}")
            return group

        target_dir.mkdir(parents=True, exist_ok=True)
        moved: List[Path] = []
        new_files: List[Path] = []

        try:
            for f in group.all_files():
                if not f.exists():
                    continue
                dest = target_dir / f.name
                if dest.exists() and dest.resolve() == f.resolve():
                    new_files.append(dest)
                    continue
                shutil.move(str(f), str(dest))
                moved.append(dest)
                new_files.append(dest)
                logger.debug(f"Moved {f} → {dest}")

            new_primary = target_dir / group.primary.name
            new_sidecars = [target_dir / s.name for s in group.sidecars]
            logger.info(f"Moved {group.primary.name} + {len(group.sidecars)} sidecars → {target_dir}")
            return FileGroup(primary=new_primary, sidecars=new_sidecars)

        except Exception as exc:
            logger.error(f"Move failed for {group.primary}: {exc}")
            # Best-effort rollback
            for dest in moved:
                try:
                    original = group.primary.parent / dest.name
                    if dest.exists() and not original.exists():
                        shutil.move(str(dest), str(original))
                except Exception as rollback_exc:
                    logger.error(f"Rollback failed for {dest}: {rollback_exc}")
            return None

    def _copy_group(self, group: FileGroup, target_dir: Path) -> Optional[FileGroup]:
        """Copy all files in group to target_dir, return new FileGroup."""
        if not group.exists():
            logger.warning(f"Primary file does not exist: {group.primary}")
            return None

        if self.dry_run:
            logger.info(f"DRY RUN copy: {group.primary.name} + {len(group.sidecars)} sidecars → {target_dir}")
            return group

        target_dir.mkdir(parents=True, exist_ok=True)
        new_files: List[Path] = []

        try:
            for f in group.all_files():
                if not f.exists():
                    continue
                dest = target_dir / f.name
                shutil.copy2(str(f), str(dest))
                new_files.append(dest)
                logger.debug(f"Copied {f} → {dest}")

            new_primary = target_dir / group.primary.name
            new_sidecars = [target_dir / s.name for s in group.sidecars]
            logger.info(f"Copied {group.primary.name} + {len(group.sidecars)} sidecars → {target_dir}")
            return FileGroup(primary=new_primary, sidecars=new_sidecars)

        except Exception as exc:
            logger.error(f"Copy failed for {group.primary}: {exc}")
            return None
