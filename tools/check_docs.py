#!/usr/bin/env python3
"""Fail CI if any task or documentation page is missing its EN/FR counterpart."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs' / 'site'

def get_types():
    main = ast.parse((ROOT/'app/main.py').read_text(encoding='utf-8'))
    for node in main.body:
        if isinstance(node, ast.Assign) and any(isinstance(t,ast.Name) and t.id=='VALID_TYPES' for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError('VALID_TYPES not found')


def check_docs():
    names = {x.name for x in DOCS.glob('*.md')}
    english = {n for n in names if not n.endswith('.fr.md')}
    french = {n.removesuffix('.fr.md')+'.md' for n in names if n.endswith('.fr.md')}
    assert english == french, f'Missing translations: EN-only={english-french}; FR-only={french-english}'
    assert len(english) >= 12, 'Documentation must be comprehensive and multipage'
    assert 'default: true' in (ROOT/'mkdocs.yml').read_text(), 'English must be default'
    types=get_types()
    for slug in ('tasks.md','tasks.fr.md'):
        content=(DOCS/slug).read_text(encoding='utf-8')
        missing = {t for t in types if f'`{t}`' not in content}
        assert not missing, f'{slug} missing implemented adapters: {missing}'
    # Check that all local relative Markdown links resolve within documentation tree.
    for page in DOCS.glob('*.md'):
        content=page.read_text(encoding='utf-8')
        assert len(content.strip()) > 500, f'{page.name}: page too short'
        for link in re.findall(r'\]\(([^)]+)\)', content):
            if link.startswith(('https://', 'http://','#','mailto:')): continue
            path = link.split('#', 1)[0]
            if not path: continue
            assert (page.parent/path).exists(), f'{page.name}: broken link to {path}'
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    for fragment in ('mkdocs.yml','docs/site/index.md','docs/site/index.fr.md'):
        assert fragment in readme, f'README missing link: {fragment}'
    changelog=(DOCS/'changelog.md').read_text()
    assert '0.2.0' in changelog and '0.2.0' in (DOCS/'changelog.fr.md').read_text()
    print(f'OK: {len(english)} EN + {len(french)} FR documentation pages; {len(types)} adapter types documented in both languages')

if __name__ == '__main__':
    check_docs()
