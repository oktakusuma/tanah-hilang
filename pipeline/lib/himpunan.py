"""Definisi himpunan konsesi (SKEMA.md §1).

`minerba` = batubara + mineral logam (825 konsesi) — himpunan tesis, dipakai app.
`lengkap` = semua WIUP Kalimantan termasuk mineral bukan logam / batuan (galian C), 1.765.
Daftar komoditas disalin dari scripts/filter_minerba.py::MINERBA_COMMODITIES (pipeline arsip)
supaya kedua pipeline menyaring himpunan yang sama persis.
"""
from __future__ import annotations

# Sumber kebenaran lama; impor dinamis supaya tak ada dua daftar yang bisa berbeda.
try:  # pragma: no cover — hanya jalan di repo utuh
    import importlib.util as _iu
    from pathlib import Path as _P
    _p = _P(__file__).resolve().parents[2] / "scripts" / "filter_minerba.py"
    _spec = _iu.spec_from_file_location("filter_minerba_arsip", _p)
    _mod = _iu.module_from_spec(_spec); _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
    MINERBA_KOMODITAS = frozenset(_mod.MINERBA_COMMODITIES)
except Exception as _e:  # noqa: BLE001
    raise SystemExit(f"GAGAL memuat daftar komoditas minerba dari scripts/filter_minerba.py: {_e}")


def termasuk(himpunan: str, komoditas: str | None) -> bool:
    if himpunan == "lengkap":
        return True
    if himpunan == "minerba":
        return (komoditas or "").strip().upper() in MINERBA_KOMODITAS
    raise ValueError(himpunan)
