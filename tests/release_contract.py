"""Check retained behavior through the actual coordinator inheritance chain."""
import ast
import json


def release_chain_source(component):
    module = "__init__"
    visited, sources = set(), []
    while module not in visited:
        visited.add(module)
        source = (component / (module + ".py")).read_text(encoding="utf-8")
        sources.append(source)
        tree = ast.parse(source)
        imports = {alias.asname or alias.name: node.module
                   for node in tree.body if isinstance(node, ast.ImportFrom) and node.level == 1
                   for alias in node.names if alias.name == "CasaESEnergyCoordinator"}
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CasaESEnergyCoordinator"]
        if classes:
            bases = [b.id for b in classes[0].bases if isinstance(b, ast.Name)]
            parent = next((imports[b] for b in bases if b in imports), None)
        else:
            parent = imports.get("CasaESEnergyCoordinator")
        if not parent:
            return "\n".join(sources)
        if not parent.startswith("coordinator"):
            raise AssertionError("Unexpected coordinator parent: " + parent)
        module = parent
    raise AssertionError("Coordinator inheritance cycle")


def assert_release_version(test, component, minimum):
    manifest = json.loads((component / "manifest.json").read_text(encoding="utf-8"))
    tree = ast.parse((component / "const.py").read_text(encoding="utf-8"))
    constant = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "VERSION" for t in n.targets))
    test.assertEqual(manifest["version"], constant)
    test.assertGreaterEqual(tuple(map(int, constant.split("."))), tuple(map(int, minimum.split("."))))

