from __future__ import annotations

import ast
from pathlib import Path


def test_dispatcher_google_oauth_is_not_excluded_from_spedi_build() -> None:
    spec = Path(__file__).resolve().parents[1] / 'Spedi.spec'
    source = spec.read_text(encoding='utf-8')
    tree = ast.parse(source, filename=str(spec))
    analysis = next(
        node.value for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'a' for target in node.targets)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == 'Analysis'
    )
    excludes = next(
        ast.literal_eval(keyword.value)
        for keyword in analysis.keywords
        if keyword.arg == 'excludes'
    )
    assert 'google_auth_oauthlib' not in excludes
    assert '"google_auth_oauthlib.flow"' in source
