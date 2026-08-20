"""验证合成兼容协议 fixture 的 SHA256 清单与原始 bytes 一致。"""

import hashlib
from pathlib import Path

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "compatibility" / "synthetic"


def test_synthetic_fixture_sha256sums_covers_exact_protocol_bytes() -> None:
    """验证 SHA256SUMS 无缺项、重复项或额外项，防止换行被静默改写。"""
    sums_path = FIXTURE_ROOT / "SHA256SUMS"
    expected: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        digest, relative_path = line.split("  ", 1)
        assert relative_path not in expected
        expected[relative_path] = digest
    actual_paths = {
        path.relative_to(FIXTURE_ROOT).as_posix() for path in FIXTURE_ROOT.rglob("*.txt")
    }
    assert set(expected) == actual_paths
    for relative_path, expected_digest in expected.items():
        actual_digest = hashlib.sha256((FIXTURE_ROOT / relative_path).read_bytes()).hexdigest()
        assert actual_digest == expected_digest
