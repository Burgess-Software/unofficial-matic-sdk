from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

_PYTHON_FENCE = re.compile(r"```python\n(.*?)```", re.DOTALL)
_MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_REPOSITORY_BLOB_PREFIX = (
    "https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/"
)


def _documents() -> tuple[Path, ...]:
    return (Path("README.md"), *sorted(Path("docs").glob("*.md")))


def test_documented_python_compiles_and_imports_public_names() -> None:
    checked = 0
    for document in _documents():
        text = document.read_text(encoding="utf-8")
        for index, source in enumerate(_PYTHON_FENCE.findall(text), start=1):
            filename = f"{document}:python-block-{index}"
            tree = compile(
                source,
                filename,
                "exec",
                flags=ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
            )
            assert isinstance(tree, ast.Module)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.module is None:
                    continue
                if not node.module.startswith("matic_sdk"):
                    continue
                module = importlib.import_module(node.module)
                for imported in node.names:
                    assert hasattr(module, imported.name), (
                        f"{filename} imports missing {node.module}.{imported.name}"
                    )
            checked += 1
    assert checked >= 10


def test_local_documentation_links_resolve() -> None:
    checked = 0
    for document in _documents():
        text = document.read_text(encoding="utf-8")
        for raw_target in _MARKDOWN_LINK.findall(text):
            target = raw_target.split("#", 1)[0]
            if not target:
                continue
            if target.startswith(_REPOSITORY_BLOB_PREFIX):
                destination = Path(target.removeprefix(_REPOSITORY_BLOB_PREFIX))
            elif "://" not in target:
                destination = document.parent / target
            else:
                continue
            assert destination.exists(), f"{document} links to missing {destination}"
            checked += 1
    assert checked >= 20


def test_public_readme_uses_installed_cli_and_keeps_both_map_images() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "uv run matic" not in readme
    assert "58efbb9e-4180-4c8d-b38f-ff300f1c86b7" in readme
    assert "df910ffe-de7b-4bc0-bf21-1c3847c25a78" in readme
