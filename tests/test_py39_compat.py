"""Every script and test must run on Python 3.9 (the oldest version the skills support).

CI runs the suite on 3.9 as well; these checks catch the usual mistakes on any interpreter:

- syntax: every .py file parses with ast feature_version (3, 9) (match statements, newer
  f-string forms and other grammar the 3.9 parser rejects);
- runtime APIs newer than 3.9, found in the syntax tree: `X | Y` type unions evaluated at
  run time (isinstance, casts, annotations in a module without
  `from __future__ import annotations`), zip(strict=), Path.read_text / write_text
  (newline=), dataclass(slots= / kw_only=), int.bit_count, itertools.pairwise / batched,
  bisect key=, aiter / anext, contextlib.aclosing / chdir, typing.get_type_hints, 3.10+
  typing names, glob root_dir= / include_hidden=, subprocess process_group= / pipesize=,
  open(encoding="locale"), sys.orig_argv, datetime.UTC, enum.StrEnum, hashlib.file_digest,
  tomllib, asyncio.TaskGroup / timeout.

The list is a net for common slips, not a proof; the CI job on 3.9 is the real check.
"""
import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", "rv_frames"}


def _python_files():
    out = []
    for p in sorted(REPO_ROOT.rglob("*.py")):
        rel = p.relative_to(REPO_ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        out.append(p)
    return out


PY_FILES = _python_files()
IDS = [p.relative_to(REPO_ROOT).as_posix() for p in PY_FILES]

# names and attributes that only exist from 3.10 on (or later)
NEW_FUNCTIONS = {"aiter", "anext"}
NEW_MODULES = {"tomllib"}
NEW_ATTRIBUTES = {
    ("itertools", "pairwise"), ("itertools", "batched"), ("contextlib", "aclosing"),
    ("contextlib", "chdir"), ("typing", "get_type_hints"), ("typing", "TypeAlias"),
    ("typing", "ParamSpec"), ("typing", "Concatenate"), ("typing", "TypeGuard"),
    ("typing", "Self"), ("typing", "LiteralString"), ("typing", "Never"),
    ("typing", "Required"), ("typing", "NotRequired"), ("typing", "Unpack"),
    ("typing", "assert_never"), ("typing", "reveal_type"), ("typing", "override"),
    ("sys", "orig_argv"), ("sys", "stdlib_module_names"), ("datetime", "UTC"),
    ("enum", "StrEnum"), ("hashlib", "file_digest"), ("asyncio", "TaskGroup"),
    ("asyncio", "timeout"), ("asyncio", "Runner"), ("logging", "getLevelNamesMapping"),
    ("math", "cbrt"), ("math", "exp2"),
}
NEW_METHODS = {"bit_count", "hardlink_to"}
NEW_KEYWORDS = {           # call name (last part) -> keywords added after 3.9
    "zip": {"strict"},
    "read_text": {"newline"},
    "write_text": {"newline"},
    "dataclass": {"slots", "kw_only", "match_args"},
    "bisect": {"key"}, "bisect_left": {"key"}, "bisect_right": {"key"},
    "insort": {"key"}, "insort_left": {"key"}, "insort_right": {"key"},
    "glob": {"root_dir", "dir_fd", "include_hidden"},
    "iglob": {"root_dir", "dir_fd", "include_hidden"},
    "run": {"process_group", "pipesize"}, "Popen": {"process_group", "pipesize"},
    "check_output": {"process_group", "pipesize"}, "call": {"process_group", "pipesize"},
}
TYPE_NAMES = {"int", "str", "float", "bool", "bytes", "list", "dict", "tuple", "set",
              "frozenset", "type", "object", "complex", "bytearray", "Path"}


def _call_name(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_type_operand(node):
    if isinstance(node, ast.Constant) and node.value is None:
        return True
    if isinstance(node, ast.Name) and node.id in TYPE_NAMES:
        return True
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and \
            node.value.id in TYPE_NAMES | {"Optional", "Union", "List", "Dict", "Tuple"}:
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _is_type_operand(node.left) or _is_type_operand(node.right)
    return False


def _annotation_nodes(tree):
    """Ids of every node that sits inside an annotation."""
    ids = set()
    for node in ast.walk(tree):
        anns = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            anns.append(node.returns)
            a = node.args
            for arg in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]:
                if arg is not None:
                    anns.append(arg.annotation)
        elif isinstance(node, ast.AnnAssign):
            anns.append(node.annotation)
        for ann in anns:
            if ann is not None:
                ids.update(id(n) for n in ast.walk(ann))
    return ids


def py39_problems(src, name="<src>"):
    """Uses of Python 3.10+ features in source text: [(line, message)]."""
    tree = ast.parse(src, filename=name, feature_version=(3, 9))
    future_annotations = any(
        isinstance(n, ast.ImportFrom) and n.module == "__future__" and
        any(a.name == "annotations" for a in n.names) for n in tree.body)
    in_annotation = _annotation_nodes(tree)
    probs = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr) and \
                _is_type_operand(node):
            if id(node) not in in_annotation or not future_annotations:
                probs.append((line, "X | Y type union evaluated at run time (3.10+); use "
                                    "Optional / Union or from __future__ import annotations"))
        elif isinstance(node, ast.Call):
            name_ = _call_name(node.func)
            if name_ in NEW_FUNCTIONS and isinstance(node.func, ast.Name):
                probs.append((line, f"{name_}() is 3.10+"))
            for kw in node.keywords:
                if kw.arg and kw.arg in NEW_KEYWORDS.get(name_, ()):
                    probs.append((line, f"{name_}({kw.arg}=...) is 3.10+"))
                if kw.arg == "encoding" and isinstance(kw.value, ast.Constant) and \
                        kw.value.value == "locale":
                    probs.append((line, 'encoding="locale" is 3.10+'))
            if name_ in NEW_METHODS and isinstance(node.func, ast.Attribute):
                probs.append((line, f".{name_}() is 3.10+"))
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and \
                (node.value.id, node.attr) in NEW_ATTRIBUTES:
            probs.append((line, f"{node.value.id}.{node.attr} is 3.10+"))
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                if (node.module, a.name) in NEW_ATTRIBUTES:
                    probs.append((line, f"from {node.module} import {a.name} is 3.10+"))
            if node.module in NEW_MODULES:
                probs.append((line, f"module {node.module} is 3.11+"))
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name in NEW_MODULES:
                    probs.append((line, f"module {a.name} is 3.11+"))
    return probs


def test_there_are_python_files_to_check():
    names = {p.name for p in PY_FILES}
    assert {"rv_review.py", "rvio_cmd.py", "check_repo.py", "conftest.py"} <= names


@pytest.mark.parametrize("path", PY_FILES, ids=IDS)
def test_parses_as_python_39(path):
    src = path.read_text(encoding="utf-8")
    try:
        ast.parse(src, filename=str(path), feature_version=(3, 9))
    except SyntaxError as e:
        pytest.fail(f"{path.name}:{e.lineno}: not Python 3.9 syntax: {e.msg}")


@pytest.mark.parametrize("path", PY_FILES, ids=IDS)
def test_no_python_310_runtime_apis(path):
    probs = py39_problems(path.read_text(encoding="utf-8"), str(path))
    assert not probs, "\n".join(f"{path.name}:{ln}: {msg}" for ln, msg in probs)


# the checker itself: it must flag each pattern and leave the 3.9-safe forms alone

@pytest.mark.parametrize("src", [
    "x = isinstance(1, int | str)",
    "def f(a: int | None = None):\n    return a",
    "x: str | None = None",
    "for a, b in zip([1], [2], strict=True):\n    pass",
    "from pathlib import Path\nPath('a').write_text('x', newline='\\n')",
    "from pathlib import Path\nPath('a').read_text(newline='')",
    "from dataclasses import dataclass\n@dataclass(slots=True)\nclass A:\n    x: int = 0",
    "import itertools\nlist(itertools.pairwise([1, 2]))",
    "from itertools import pairwise",
    "n = (5).bit_count()",
    "import bisect\nbisect.bisect_left([1], 1, key=abs)",
    "async def f(it):\n    return await anext(it)",
    "import typing\ntyping.get_type_hints(object)",
    "from typing import TypeAlias",
    "import glob\nglob.glob('*', root_dir='.')",
    "open('a', encoding='locale')",
    "import tomllib",
    "import datetime\ndatetime.UTC",
    "import subprocess\nsubprocess.run(['x'], process_group=0)",
])
def test_checker_flags_newer_features(src):
    assert py39_problems(src)


@pytest.mark.parametrize("src", [
    "from __future__ import annotations\ndef f(a: int | None = None) -> list[str] | None:\n    return None",
    "from __future__ import annotations\nx: str | None = None",
    "flags = 1 | 2",
    "import subprocess\nf = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP",
    "s = {1} | {2}",
    "from typing import Optional\ndef f(a: Optional[int] = None):\n    return a",
    "x = 'abc'.removeprefix('a')",
    "for a, b in zip([1], [2]):\n    pass",
    "from pathlib import Path\nPath('a').write_text('x', encoding='utf-8')",
    "def g(strict=None):\n    return strict\ng(strict=True)",
    "max([], default=-1)",
    "import functools\n@functools.lru_cache(maxsize=None)\ndef f():\n    return 1",
])
def test_checker_accepts_39_forms(src):
    assert py39_problems(src) == []


@pytest.mark.parametrize("src", [
    "match x:\n    case 1:\n        pass",
])
def test_checker_rejects_310_syntax(src):
    with pytest.raises(SyntaxError):
        py39_problems(src)
