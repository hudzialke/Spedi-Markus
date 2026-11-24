from tools.bundle_helpers import prune_private_ocr_duplicates


def test_shared_ocr_libraries_and_their_dependencies_are_retained(tmp_path):
    private = tmp_path/'tesseract'
    private.mkdir()
    binaries = []
    for name in ('private.dll', 'shared.dll', 'transitive.dll', 'different.dll'):
        (private/name).write_bytes(name.encode())
        source = tmp_path/name
        source.write_bytes(b'different source' if name == 'different.dll' else name.encode())
        binaries.append((name, str(source), 'BINARY'))
    application = tmp_path/'application.pyd'
    application.write_bytes(b'fixture')
    binaries.append(('application.pyd', str(application), 'EXTENSION'))
    graph = {'application.pyd': {'shared.dll'}, 'shared.dll': {'transitive.dll'},
             'transitive.dll': set(), 'private.dll': set(), 'different.dll': set()}
    from pathlib import Path
    filtered, removed = prune_private_ocr_duplicates(binaries, [], private,
        read_imports=lambda path: graph[Path(path).name])
    assert removed == ['private.dll']
    assert {entry[0] for entry in filtered} == {'shared.dll', 'transitive.dll', 'different.dll', 'application.pyd'}
    assert all((private/name).exists() for name in graph if name.endswith('.dll'))


def test_unknown_native_dependency_keeps_all_original_binaries(tmp_path):
    private = tmp_path/'tesseract'
    private.mkdir()
    (private/'private.dll').write_bytes(b'identical')
    candidate = tmp_path/'private.dll'
    candidate.write_bytes(b'identical')
    entries = [('private.dll', str(candidate), 'BINARY'), ('external.pyd', 'external.pyd', 'EXTENSION')]
    def fail(path):
        raise ValueError('unknown PE format')
    assert prune_private_ocr_duplicates(entries, [], private, read_imports=fail) == (entries, [])
