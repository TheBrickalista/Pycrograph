# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))          # tests/synth.py
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from synth import metamorph_xml, reference_like_plane, write_metamorph_tiff, write_stk  # noqa: E402


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A folder name with spaces, like the real /Volumes/LaCie tree."""
    d = tmp_path / "LaCie" / "IF 2025 nuclei" / "exp 1"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def mm_tiff(data_dir: Path) -> Path:
    """Reference-like single-plane MetaMorph TIFF (Nuc_ER_ER.tif lookalike)."""
    return write_metamorph_tiff(data_dir / "Nuc_ER_ER.tif", [reference_like_plane(64, 64)],
                                [metamorph_xml(width=64, height=64)])


@pytest.fixture
def stk_tc(data_dir: Path) -> Path:
    """STK, 3 time points x 2 interleaved channels (488/561), planes numbered."""
    planes = np.stack([np.full((24, 32), 1000 + 100 * i, np.uint16) for i in range(6)])
    planes[:, 0, 0] = np.arange(6)                       # plane id in a corner
    return write_stk(data_dir / "timelapse 2ch.stk", planes, wavelengths=[488, 561] * 3,
                     times_ms=[0, 50, 30000, 30050, 60000, 60050])


@pytest.fixture
def stk_z(data_dir: Path) -> Path:
    planes = np.stack([np.full((16, 16), 500 + i, np.uint16) for i in range(5)])
    return write_stk(data_dir / "zstack.stk", planes, z_distances=[0.5] * 5)


@pytest.fixture
def workspace(tmp_path: Path):
    from pycrograph.storage import Workspace
    ws = Workspace(tmp_path / "ws")
    ws.ensure()
    return ws
