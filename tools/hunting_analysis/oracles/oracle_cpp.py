"""Subprocess wrapper for the C++ oracle shim.

Spawns oracle_cpp/oracle as a long-lived subprocess and issues line-based
RPCs over stdin/stdout. The oracle is the ground truth for damage arithmetic;
the Python model's melee/block implementation is property-tested against it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ORACLE_DIR = Path(__file__).resolve().parent / "cpp_shim"
ORACLE_BIN = ORACLE_DIR / "oracle"


def ensure_built() -> bool:
    if ORACLE_BIN.exists():
        return True
    src = ORACLE_DIR / "oracle.cpp"
    if not src.exists():
        return False
    res = subprocess.run(
        ["g++", "-O2", "-std=c++14", str(src), "-o", str(ORACLE_BIN)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        print("oracle build failed:\n" + res.stderr)
        return False
    return True


class CppOracle:
    def __init__(self):
        if not ensure_built():
            raise RuntimeError("cannot build C++ oracle (g++ missing?)")
        self.proc = subprocess.Popen(
            [str(ORACLE_BIN)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
        )

    def _call(self, line: str) -> str:
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        out = self.proc.stdout.readline().strip()
        return out

    def max_weapon(self, level: int, skill: int, attack: int, factor: float) -> int:
        return int(self._call(f"max_weapon {level} {skill} {attack} {factor}"))

    def max_melee(self, skill: int, attack: int) -> int:
        return int(self._call(f"max_melee {skill} {attack}"))

    def ping(self) -> str:
        return self._call("ping")

    def close(self):
        try:
            if self.proc.stdin:
                self.proc.stdin.write("quit\n")
                self.proc.stdin.flush()
        except BrokenPipeError:
            pass
        self.proc.wait(timeout=2)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
