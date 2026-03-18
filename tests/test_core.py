"""Tests for sidecarFS core operations."""

import pytest
from pathlib import Path

from sidecars import SidecarFS, FileGroup


@pytest.fixture
def tmp_fs(tmp_path):
    """Return a SidecarFS instance with a trash root under tmp_path."""
    return SidecarFS(trash_root=tmp_path / "_trash", dry_run=False)


@pytest.fixture
def video_group(tmp_path):
    """Create a primary .mp4 with .md and .properties sidecars."""
    primary = tmp_path / "episode.mp4"
    md = tmp_path / "episode.md"
    props = tmp_path / "episode.properties"
    primary.write_bytes(b"\x00" * 1024)
    md.write_text("# Episode\nsome notes")
    props.write_text("sender=ARD\nthema=Tatort\n")
    return primary


def test_find_group_discovers_sidecars(tmp_fs, video_group, tmp_path):
    group = tmp_fs.find_group(video_group)
    assert group.primary == video_group
    sidecar_names = {s.name for s in group.sidecars}
    assert "episode.md" in sidecar_names
    assert "episode.properties" in sidecar_names


def test_move_transfers_all_files(tmp_fs, video_group, tmp_path):
    target_dir = tmp_path / "dest"
    result = tmp_fs.move(video_group, target_dir)
    assert result is not None
    assert (target_dir / "episode.mp4").exists()
    assert (target_dir / "episode.md").exists()
    assert (target_dir / "episode.properties").exists()
    # Original files should be gone
    assert not video_group.exists()


def test_copy_leaves_originals_intact(tmp_fs, video_group, tmp_path):
    target_dir = tmp_path / "copy_dest"
    result = tmp_fs.copy(video_group, target_dir)
    assert result is not None
    assert (target_dir / "episode.mp4").exists()
    # Original must still exist
    assert video_group.exists()


def test_trash_moves_to_trash_folder(tmp_fs, video_group, tmp_path):
    result = tmp_fs.trash(video_group)
    assert result is not None
    # Primary should no longer be at original location
    assert not video_group.exists()
    # Trashed primary must exist somewhere under _trash/
    trash_root = tmp_path / "_trash"
    trashed_files = list(trash_root.rglob("episode.mp4"))
    assert len(trashed_files) == 1


def test_dry_run_does_not_move(tmp_path, video_group):
    fs = SidecarFS(trash_root=tmp_path / "_trash", dry_run=True)
    target_dir = tmp_path / "dest"
    result = fs.move(video_group, target_dir)
    # Primary still at original location
    assert video_group.exists()
    assert not (target_dir / "episode.mp4").exists()


def test_trash_requires_trash_root(tmp_path, video_group):
    fs = SidecarFS(dry_run=False)  # no trash_root
    with pytest.raises(ValueError):
        fs.trash(video_group)


def test_link_creates_relative_symlinks(tmp_fs, video_group, tmp_path):
    link_dir = tmp_path / "views" / "Season 1"
    result = tmp_fs.link(video_group, link_dir)
    assert result is not None
    primary_link = link_dir / "episode.mp4"
    assert primary_link.is_symlink()
    # Relative target — resolve() must point to original
    assert primary_link.resolve() == video_group.resolve()
    assert (link_dir / "episode.md").is_symlink()
    assert (link_dir / "episode.properties").is_symlink()


def test_link_custom_name(tmp_fs, video_group, tmp_path):
    link_dir = tmp_path / "views"
    result = tmp_fs.link(video_group, link_dir, link_name="Tatort - s2021e01.mp4")
    assert result is not None
    assert (link_dir / "Tatort - s2021e01.mp4").is_symlink()
    # Sidecars use original names
    assert (link_dir / "episode.md").is_symlink()


def test_trash_symlink_unlinks_without_trashing(tmp_path, tmp_fs):
    """Symlinks should be unlinked directly — no trash copy created."""
    # Real files in a store dir
    store_dir = tmp_path / "_store" / "ep"
    store_dir.mkdir(parents=True)
    real_video = store_dir / "video.mp4"
    real_md = store_dir / "video.md"
    real_video.write_bytes(b"\x00" * 512)
    real_md.write_text("# Episode")

    # View dir with symlinks pointing at store
    view_dir = tmp_path / "views"
    view_dir.mkdir()
    link = view_dir / "episode.mp4"
    link_md = view_dir / "episode.md"
    link.symlink_to(real_video)
    link_md.symlink_to(real_md)

    result = tmp_fs.trash(link)
    assert result is not None

    # Symlinks gone
    assert not link.exists() and not link.is_symlink()
    assert not link_md.exists() and not link_md.is_symlink()

    # Real files in _store untouched
    assert real_video.exists()
    assert real_md.exists()

    # Nothing in trash
    trash_root = tmp_path / "_trash"
    assert not trash_root.exists() or not list(trash_root.rglob("*.mp4"))


def test_edition_tag_sidecar_discovery(tmp_path, tmp_fs):
    """Sidecar without edition tag should be discovered for primary with edition tag."""
    primary = tmp_path / "Episode {edition-1080p}.mp4"
    sidecar = tmp_path / "Episode.md"
    primary.write_bytes(b"\x00" * 512)
    sidecar.write_text("# Episode")
    group = tmp_fs.find_group(primary)
    sidecar_names = {s.name for s in group.sidecars}
    assert "Episode.md" in sidecar_names
