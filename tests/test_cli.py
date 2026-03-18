"""Tests for the scfs CLI."""

import pytest
from click.testing import CliRunner
from pathlib import Path

from sidecars.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def video_dir(tmp_path):
    """Create a directory with two video files and their sidecars."""
    (tmp_path / "ep1.mp4").write_bytes(b"\x00" * 512)
    (tmp_path / "ep1.md").write_text("# Episode 1")
    (tmp_path / "ep1.properties").write_text("sender=ARD\n")
    (tmp_path / "ep2.mp4").write_bytes(b"\x00" * 256)
    # ep2 has no sidecars
    (tmp_path / "subdir").mkdir()
    return tmp_path


def test_ls_annotates_sidecars(runner, video_dir):
    result = runner.invoke(cli, ["ls", str(video_dir)])
    assert result.exit_code == 0
    lines = result.output.splitlines()
    ep1_line = next(l for l in lines if "ep1.mp4" in l)
    assert ".md" in ep1_line and ".properties" in ep1_line
    ep2_line = next(l for l in lines if "ep2.mp4" in l)
    assert "sidecar" not in ep2_line


def test_ls_does_not_list_sidecars_separately(runner, video_dir):
    result = runner.invoke(cli, ["ls", str(video_dir)])
    assert result.exit_code == 0
    # sidecar files should NOT appear as standalone entries
    assert "ep1.md" not in result.output
    assert "ep1.properties" not in result.output


def test_ls_shows_directories(runner, video_dir):
    result = runner.invoke(cli, ["ls", str(video_dir)])
    assert "subdir/" in result.output


def test_ls_single_file(runner, video_dir):
    result = runner.invoke(cli, ["ls", str(video_dir / "ep1.mp4")])
    assert result.exit_code == 0
    assert "ep1.mp4" in result.output
    assert ".md" in result.output


def test_mv_moves_with_sidecars(runner, video_dir, tmp_path):
    dest = tmp_path / "dest"
    result = runner.invoke(cli, ["mv", str(video_dir / "ep1.mp4"), str(dest)])
    assert result.exit_code == 0
    assert (dest / "ep1.mp4").exists()
    assert (dest / "ep1.md").exists()
    assert (dest / "ep1.properties").exists()
    assert not (video_dir / "ep1.mp4").exists()


def test_mv_dry_run_does_not_move(runner, video_dir, tmp_path):
    dest = tmp_path / "dest"
    result = runner.invoke(cli, ["mv", "--dry-run", str(video_dir / "ep1.mp4"), str(dest)])
    assert result.exit_code == 0
    assert "Would move" in result.output
    assert (video_dir / "ep1.mp4").exists()  # untouched


def test_cp_copies_with_sidecars(runner, video_dir, tmp_path):
    dest = tmp_path / "dest"
    result = runner.invoke(cli, ["cp", str(video_dir / "ep1.mp4"), str(dest)])
    assert result.exit_code == 0
    assert (dest / "ep1.mp4").exists()
    assert (dest / "ep1.md").exists()
    # Original still present
    assert (video_dir / "ep1.mp4").exists()


def test_rm_moves_to_trash(runner, video_dir, tmp_path):
    trash = tmp_path / "_trash"
    result = runner.invoke(cli, ["rm", "--trash-root", str(trash), str(video_dir / "ep1.mp4")])
    assert result.exit_code == 0
    assert not (video_dir / "ep1.mp4").exists()
    trashed = list(trash.rglob("ep1.mp4"))
    assert len(trashed) == 1
    # Sidecar must also be in trash
    trashed_md = list(trash.rglob("ep1.md"))
    assert len(trashed_md) == 1


def test_rm_dry_run_does_not_trash(runner, video_dir, tmp_path):
    trash = tmp_path / "_trash"
    result = runner.invoke(cli, ["rm", "--dry-run", "--trash-root", str(trash), str(video_dir / "ep1.mp4")])
    assert result.exit_code == 0
    assert "Would trash" in result.output
    assert (video_dir / "ep1.mp4").exists()


def test_rm_multiple_files(runner, video_dir, tmp_path):
    trash = tmp_path / "_trash"
    result = runner.invoke(cli, [
        "rm", "--trash-root", str(trash),
        str(video_dir / "ep1.mp4"),
        str(video_dir / "ep2.mp4"),
    ])
    assert result.exit_code == 0
    assert not (video_dir / "ep1.mp4").exists()
    assert not (video_dir / "ep2.mp4").exists()


def test_ln_creates_symlinks_with_sidecars(runner, video_dir, tmp_path):
    link_dir = tmp_path / "views" / "Season 1"
    result = runner.invoke(cli, ["ln", str(video_dir / "ep1.mp4"), str(link_dir)])
    assert result.exit_code == 0
    primary_link = link_dir / "ep1.mp4"
    assert primary_link.is_symlink()
    assert primary_link.resolve() == (video_dir / "ep1.mp4").resolve()
    # Sidecar symlinks must also exist
    assert (link_dir / "ep1.md").is_symlink()
    assert (link_dir / "ep1.properties").is_symlink()


def test_ln_custom_name(runner, video_dir, tmp_path):
    link_dir = tmp_path / "views"
    custom = "Tatort - s2021e01 - Title.mp4"
    result = runner.invoke(cli, ["ln", str(video_dir / "ep1.mp4"), str(link_dir), "--name", custom])
    assert result.exit_code == 0
    assert (link_dir / custom).is_symlink()
    # Sidecars keep their original names
    assert (link_dir / "ep1.md").is_symlink()


def test_ln_dry_run_creates_nothing(runner, video_dir, tmp_path):
    link_dir = tmp_path / "views"
    result = runner.invoke(cli, ["ln", "--dry-run", str(video_dir / "ep1.mp4"), str(link_dir)])
    assert result.exit_code == 0
    assert "Would link" in result.output
    assert not link_dir.exists()
