"""Remove proven private OCR duplicates while retaining shared native libraries."""
from pathlib import Path, PurePosixPath
import hashlib


def _imports(path):
    import pefile
    image = pefile.PE(str(path), fast_load=True)
    try:
        image.parse_data_directories(directories=[
            pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT'],
            pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT'],
        ])
        return {entry.dll.decode('ascii').lower()
                for section in ('DIRECTORY_ENTRY_IMPORT', 'DIRECTORY_ENTRY_DELAY_IMPORT')
                for entry in getattr(image, section, [])}
    finally:
        image.close()


def _digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').digest()


def prune_private_ocr_duplicates(binaries, datas, ocr_root, *, read_imports=_imports):
    """Prune only byte-identical root DLLs not used outside tesseract/.

    Keep the full OCR runtime in its own folder. Any uncertain PE dependency
    prevents pruning, so an unsupported binary cannot break the bundle.
    """
    ocr_root = Path(ocr_root)
    candidates = {}
    for entry in binaries:
        destination, source, *_ = entry
        name = PurePosixPath(destination.replace('\\', '/'))
        private = ocr_root/name.name
        if (len(name.parts) == 1 and name.suffix.lower() == '.dll'
                and private.is_file() and _digest(source) == _digest(private)):
            candidates[name.name.lower()] = entry
    if not candidates:
        return list(binaries), []
    try:
        needed = set()
        for destination, source, *_ in [*binaries, *datas]:
            name = PurePosixPath(destination.replace('\\', '/'))
            if (name.parts[0].lower() == 'tesseract' or name.suffix.lower() not in {'.dll', '.pyd', '.exe'}
                    or (len(name.parts) == 1 and name.name.lower() in candidates)):
                continue
            needed.update(read_imports(source) & candidates.keys())
        pending = list(needed)
        while pending:
            dependencies = read_imports(candidates[pending.pop()][1]) & candidates.keys()
            for dependency in dependencies-needed:
                needed.add(dependency)
                pending.append(dependency)
    except Exception:
        return list(binaries), []
    removed = {entry[0] for name, entry in candidates.items() if name not in needed}
    return [entry for entry in binaries if entry[0] not in removed], sorted(removed)
