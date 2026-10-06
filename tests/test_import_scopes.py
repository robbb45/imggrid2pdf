"""Catch imported-module shadowing that compilation alone does not detect."""

import ast
import symtable
import unittest
from pathlib import Path


def module_shadowing(source):
    module_names = set()

    class ModuleImports(ast.NodeVisitor):
        def visit_Import(self, node):
            module_names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)

        def visit_FunctionDef(self, node):
            pass

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

    ModuleImports().visit(ast.parse(source))
    issues = []

    def inspect(table):
        if table.get_type() == "function":
            for symbol in table.get_symbols():
                if (symbol.get_name() in module_names and symbol.is_local()
                        and symbol.is_referenced() and not symbol.is_imported()):
                    issues.append((table.get_name(), symbol.get_name(), table.get_lineno()))
        for child in table.get_children():
            inspect(child)

    inspect(symtable.symtable(source, "<source>", "exec"))
    return issues


class ImportScopeTests(unittest.TestCase):
    def test_detects_module_shadowing_before_assignment(self):
        source = "import layout as page_layout\ndef preview(cfg):\n    page_layout.automatic(cfg)\n    for page_layout in []:\n        print(page_layout)\n"
        self.assertEqual(module_shadowing(source), [("preview", "page_layout", 2)])

    def test_distinct_names_and_local_imports_are_valid(self):
        source = "import layout as page_layout\ndef preview(cfg):\n    page_layout.automatic(cfg)\n    for page_items in []:\n        print(page_items)\ndef local():\n    import layout as page_layout\n    return page_layout.automatic({})\n"
        self.assertEqual(module_shadowing(source), [])

    def test_application_functions_do_not_shadow_imported_modules(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("ui.py", "script.py", "layout.py"):
            with self.subTest(file=name):
                self.assertEqual(module_shadowing((root / name).read_text(encoding="utf-8")), [])


if __name__ == "__main__":
    unittest.main()
