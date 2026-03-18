"""
sidecars — safe filesystem operations for files with sidecar companions.

A sidecar is any file with the same base name as the primary file but with
a companion extension (.properties, .json, .md).  All operations keep the
primary file and its sidecars in sync so they are never separated.

Guiding principles
------------------
* **No silent deletes.**  Removal always goes through a trash folder so it
  can be undone.
* **Atomic groups.**  Move/copy/trash operate on a FileGroup (primary file +
  all discovered sidecars) so partial operations are minimised.
* **Dry-run first.**  Every mutating method accepts dry_run=True which
  logs the planned operation without touching the filesystem.
"""

from .core import SidecarFS, FileGroup, SIDECAR_EXTENSIONS

__all__ = ["SidecarFS", "FileGroup", "SIDECAR_EXTENSIONS"]
__version__ = "0.1.0"
