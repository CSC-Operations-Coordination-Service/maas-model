"""
The examples of docs/telemetry.md run, and agree with each other.
"""

import pathlib
import re
from types import SimpleNamespace

import pytest

from maas_model.telemetry.testing import capture

GUIDE = pathlib.Path(__file__).resolve().parents[2] / "docs" / "telemetry.md"


def _python_blocks(heading: str) -> list:
    """the python code blocks of the section starting at heading"""
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(heading + "\n")
    following = re.compile(r"^#{1,3} ", re.M).search(text, start + len(heading))
    section = text[start : following.start() if following else len(text)]
    return re.findall(r"```python\n(.*?)```", section, re.S)


@pytest.mark.skipif(not GUIDE.exists(), reason="the guide is not shipped here")
def test_testing_example_matches_the_compute_example():
    # a datatake for an example that leaves it undefined
    namespace = {"datatake": SimpleNamespace(mission="S1", pipeline="daily")}
    blocks = _python_blocks("### Datatake compute rate")
    blocks += _python_blocks("## 6. Testing")
    assert len(blocks) == 2
    for block in blocks:
        exec(compile(block, str(GUIDE), "exec"), namespace)  # noqa: S102
    cap = capture()
    try:
        namespace["test_datatake"](cap)
    finally:
        cap.close()
