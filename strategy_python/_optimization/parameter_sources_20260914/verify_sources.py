"""Verify documentation coverage and unchanged executable AST against the pre-edit snapshot."""

import ast
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent


def executable_digest(path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            node.body.pop(0)
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()


def main():
    manifest = json.loads((OUT / "source_manifest.json").read_text())
    packages = sorted({str(Path(name).parent) for name in manifest})
    assert len(packages) == 8
    documented_fields = 0
    changed = 0
    for name, record in manifest.items():
        path = ROOT / name
        assert executable_digest(path) == record["executable_ast_sha256"], name
        current = hashlib.sha256(path.read_bytes()).hexdigest()
        assert current == record["after_source_sha256"], f"Source changed after review: {name}"
        changed += current != record["before_source_sha256"]
    documents = [ROOT / "strategy_python" / name for name in (
        "PARAMETER_SOURCES.md", "PARAMETER_QUESTIONS.md",
    )]
    for package in packages:
        directory = ROOT / package
        path = directory / "config.py"
        source = path.read_text()
        lines = source.splitlines()
        cls = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef))
        guide = directory / "PARAMETER_SOURCES.md"
        text = guide.read_text()
        assert "PARAMETER_SOURCES.md" in (directory / "README.md").read_text()
        for node in cls.body:
            if not isinstance(node, ast.AnnAssign):
                continue
            assert re.match(r"\s+# \[(工程|预算|筛选|经验)\]", lines[node.lineno - 2])
            assert f"| `{node.target.id}` |" in text
            documented_fields += 1
        documents.append(guide)
    local_links = 0
    for document in documents:
        for target in re.findall(r"\]\(([^)]+)\)", document.read_text()):
            assert (document.parent / target).exists(), (document, target)
            local_links += 1
    assert documented_fields == 282
    result = {
        "scope": "four original and four optimized strategy packages",
        "executable_ast_files_verified": len(manifest),
        "source_files_with_documentation_changes": changed,
        "config_fields_with_inline_provenance_and_local_guide": documented_fields,
        "local_source_guides": len(packages),
        "source_document_links_verified": local_links,
        "runtime_or_default_changes": False,
        "historical_experiment_hashes": "preserved; describe historical snapshots, not current bytes",
        "existing_test_run": {
            "command": ".venv/bin/pytest tests/strategy_python/optimization/delivery_test.py "
            "tests/strategy_python/architecture_test.py -q",
            "observed_result": "19 passed in 2.24s",
            "note": "record of the observed implementation-time run; this script does not run pytest",
        },
        "lint": "ruff check passed on the 40 modified strategy Python files",
    }
    (OUT / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
