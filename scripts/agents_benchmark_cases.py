"""Provider qualification fixtures with independent behavioral acceptance checks."""

from __future__ import annotations


def assignment(name, objective, paths, dependencies=()):
    return {
        "name": name,
        "objective": objective,
        "intended_paths": paths,
        "depends_on": list(dependencies),
        "verification_targets": ["unit"],
        "capabilities": ["repo_editing", "run_tests"],
    }


CASES = {
    "independent_bugs": {
        "files": {
            "cache.py": "def fresh(created, now, ttl):\n    return now - created <= ttl\n",
            "invoice.py": "def total(lines):\n    return sum(float(price) * count for price, count in lines)\n",
        },
        "assignments": [
            assignment(
                "expiry",
                "Fix cache.fresh(created, now, ttl): TTL must be positive, future creation timestamps are invalid, and expiry is exclusive. Return a bool.",
                ["cache.py"],
            ),
            assignment(
                "money",
                "Fix invoice.total(lines): prices are decimal strings, quantities are nonnegative integers (bool is invalid). Return a Decimal without float conversion, reject negative/nonfinite prices and invalid quantities with ValueError, empty input returns Decimal zero.",
                ["invoice.py"],
            ),
        ],
        "oracle": r"""
from decimal import Decimal
from cache import fresh
from invoice import total
assert [fresh(10, now, 5) for now in (9, 10, 14, 15, 16)] == [False, True, True, False, False]
assert not fresh(10, 10, 0) and not fresh(10, 10, -1)
assert total([('0.10', 3), ('0.20', 2)]) == Decimal('0.70')
assert isinstance(total([]), Decimal) and total([]) == 0
assert total([('9007199254740993.01', 2)]) == Decimal('18014398509481986.02')
for price, count in [('-1', 1), ('NaN', 1), ('Infinity', 1), ('1', -1), ('1', 1.5), ('1', True)]:
    raises(ValueError, total, [(price, count)])
""",
    },
    "feature_tests_docs": {
        "files": {
            "retry.py": "",
            "tests/test_retry.py": "",
            "README.md": "# Retry library\n",
        },
        "assignments": [
            assignment(
                "implementation",
                "Implement retry.delays(attempts, base=1.0, cap=60.0) returning a list of attempts exponential delays min(base*2**i, cap). attempts must be a nonnegative int, not bool; base/cap must be positive finite numbers, not bool. Invalid inputs raise ValueError. Support 1100 attempts without overflow.",
                ["retry.py"],
            ),
            assignment(
                "tests",
                "Create stdlib unittest tests in tests/test_retry.py for retry.delays(attempts, base=1.0, cap=60.0): capped exponential progression, zero attempts, invalid negative/bool attempts, nonpositive/nonfinite base or cap, and 1100 attempts without overflow. Tests should import retry and use unittest.main for direct execution.",
                ["tests/test_retry.py"],
            ),
            assignment(
                "documentation",
                "Document retry.delays in README.md with executable usage, defaults, cap behavior, invalid input behavior and ValueError. Match the implemented API.",
                ["README.md"],
                ["implementation"],
            ),
        ],
        "oracle": r"""
from retry import delays
assert delays(0) == []
assert delays(5, 0.25, 1) == [0.25, 0.5, 1, 1, 1]
assert delays(3, 10, 2) == [2, 2, 2]
assert delays(1100)[-1] == 60 and len(delays(1100)) == 1100
for args in [(-1,), (True,), (1.5,), (1, 0), (1, float('nan')), (1, 1, float('inf')), (1, True), (1, 1, False)]:
    raises(ValueError, delays, *args)
import unittest
suite = unittest.defaultTestLoader.discover(str(root / 'tests'), pattern='test_*.py')
assert suite.countTestCases() >= 3, 'At least three runnable regression tests required'
assert unittest.TextTestRunner().run(suite).wasSuccessful()
doc = (root / 'README.md').read_text(encoding='utf-8')
assert all(word in doc for word in ['delays', 'ValueError', 'cap', '```'])
""",
    },
    "separable_migration": {
        "files": {
            "reader.py": "def normalize(record):\n    return record\n",
            "writer.py": "def encode(name, timeout):\n    return {'name': name, 'timeout': timeout}\n",
        },
        "assignments": [
            assignment(
                "reader",
                "Migrate reader.normalize(record) to produce {'version': 2, 'service': {'name': name, 'timeout_ms': n}}. Accept legacy unversioned {'name': str, 'timeout': seconds} and v2 records. Legacy timeout seconds must be a nonnegative finite int/float, not bool, exactly representable as integer milliseconds. Name must be a nonempty string. V2 timeout_ms must be nonnegative int, not bool. Reject unknown versions/malformed inputs with ValueError, preserve input unchanged, return an independent copy.",
                ["reader.py"],
            ),
            assignment(
                "writer",
                "Migrate writer.encode(name, timeout) to emit {'version': 2, 'service': {'name': name, 'timeout_ms': n}}. timeout is legacy seconds: nonnegative finite int/float, not bool, exactly representable as integer milliseconds. Reject empty/nonstring name or invalid timeout with ValueError. Do not depend on reader.py.",
                ["writer.py"],
            ),
        ],
        "oracle": r"""
from reader import normalize
from writer import encode
import copy
expected = {'version': 2, 'service': {'name': 'search', 'timeout_ms': 1250}}
assert encode('search', 1.25) == expected
legacy = {'name': 'search', 'timeout': 1.25}
assert normalize(legacy) == expected and legacy == {'name': 'search', 'timeout': 1.25}
original = copy.deepcopy(expected)
converted = normalize(original)
converted['service']['name'] = 'changed'
assert original == expected
for value in [-1, True, float('inf'), float('nan'), 0.0001, '1']:
    raises(ValueError, encode, 'a', value)
    raises(ValueError, normalize, {'name': 'a', 'timeout': value})
for value in ['', None, 123]:
    raises(ValueError, encode, value, 1)
for value in [{}, {'version': 3}, {'version': 2, 'service': {'name': 'x', 'timeout_ms': True}}]:
    raises(ValueError, normalize, value)
""",
    },
    "independent_research": {
        "files": {
            "specs/api.md": "# API v2\nPOST /jobs accepts Idempotency-Key. Retain keys for 24 hours. A repeated key with a different payload returns 409. GET /jobs/{id} returns 404 for an unknown job. Retry 429 using Retry-After; do not retry 400.\n",
            "specs/storage.md": "# Storage journal\nUse SQLite WAL. Foreign keys must be enabled per connection. Use BEGIN IMMEDIATE for write transactions. Store money as decimal text, never floating point. Retain interrupted work for inspection; never replay an unconfirmed provider operation.\n",
            "findings/api.json": "{}\n",
            "findings/storage.json": "{}\n",
        },
        "assignments": [
            assignment(
                "api",
                "Read specs/api.md and produce findings/api.json with keys source (repository path), idempotency_header, retention_hours (int), payload_conflict_status (int), unknown_job_status (int), retryable_statuses (list of int), retry_header. Report only source-supported facts.",
                ["findings/api.json"],
            ),
            assignment(
                "storage",
                "Read specs/storage.md and produce findings/storage.json with keys source (repository path), journal_mode, foreign_keys_per_connection (bool), write_transaction, money_encoding, replay_unconfirmed (bool). Report only source-supported facts; money_encoding is 'decimal text'.",
                ["findings/storage.json"],
            ),
        ],
        "oracle": r"""
import json
api = json.loads((root / 'findings/api.json').read_text())
storage = json.loads((root / 'findings/storage.json').read_text())
assert api == {'source': 'specs/api.md', 'idempotency_header': 'Idempotency-Key', 'retention_hours': 24, 'payload_conflict_status': 409, 'unknown_job_status': 404, 'retryable_statuses': [429], 'retry_header': 'Retry-After'}
assert storage == {'source': 'specs/storage.md', 'journal_mode': 'WAL', 'foreign_keys_per_connection': True, 'write_transaction': 'BEGIN IMMEDIATE', 'money_encoding': 'decimal text', 'replay_unconfirmed': False}
""",
    },
    "review_and_fixes": {
        "files": {
            "bounds.py": "def valid_percent(value):\n    return 0 < value < 100\n",
            "access.py": "def can_read(owner, actor, public=False):\n    return public or owner == actor\n",
            "review.json": "[]\n",
        },
        "assignments": [
            assignment(
                "review",
                "Review bounds.py and access.py against these contracts: valid_percent accepts finite numeric values 0..100 inclusive but not bool; can_read permits public resources, otherwise requires both nonempty owner and actor strings equal. Write review.json as a list of objects with path, code and explanation. Use code 'inclusive-range' for missing inclusive endpoints, 'numeric-domain' for invalid numeric types, 'missing-identity' for empty/missing identities. Do not modify code.",
                ["review.json"],
            ),
            assignment(
                "fix-bounds",
                "Fix bounds.valid_percent(value): return bool, accept finite int/float 0..100 inclusive, reject bool and all other types by returning False. Never raise for invalid values.",
                ["bounds.py"],
            ),
            assignment(
                "fix-access",
                "Fix access.can_read(owner, actor, public=False): return bool, permit public=True, otherwise require both nonempty string identities that are equal. Missing identities must never authenticate.",
                ["access.py"],
            ),
        ],
        "oracle": r"""
from bounds import valid_percent
from access import can_read
import json
for value in [0, 100, 50.5]:
    assert valid_percent(value) is True
for value in [-1, 101, True, None, '50', float('nan'), float('inf')]:
    assert valid_percent(value) is False
assert can_read('a', 'a') is True and can_read('a', 'b') is False
for owner, actor in [(None, None), ('', ''), (1, 1), ('a', None)]:
    assert can_read(owner, actor) is False and can_read(owner, actor, public=True) is True
review = json.loads((root / 'review.json').read_text())
assert {(r['path'], r['code']) for r in review} >= {('bounds.py', 'inclusive-range'), ('bounds.py', 'numeric-domain'), ('access.py', 'missing-identity')}
assert all(isinstance(r['explanation'], str) and len(r['explanation']) > 15 for r in review)
""",
    },
    "several_issues": {
        "files": {
            "pages.py": "def paginate(items, size):\n    return [items]\n",
            "secrets.py": "def redact(record):\n    return record\n",
            "export.py": "def csv_row(values):\n    return ','.join(values)\n",
        },
        "assignments": [
            assignment(
                "pagination",
                "Implement pages.paginate(items, size): consume an iterable once, return lists of up to size items; no trailing empty page; empty input -> []. size must be positive int, not bool, otherwise ValueError.",
                ["pages.py"],
            ),
            assignment(
                "redaction",
                "Implement secrets.redact(record) recursively over dict/list without mutating input. Case-insensitive dict keys password, token, api_key replace their entire value with '[REDACTED]'. Other values/keys preserved. Inputs contain JSON values only.",
                ["secrets.py"],
            ),
            assignment(
                "csv",
                "Implement export.csv_row(values) for strings using standard CSV quoting: commas, quotes, CR/LF correctly escaped, double inner quotes, terminate exactly with CRLF. Empty list emits CRLF. Must round-trip through csv.reader.",
                ["export.py"],
            ),
        ],
        "oracle": r"""
from pages import paginate
from secrets import redact
from export import csv_row
import copy, csv, io
assert paginate(iter(range(5)), 2) == [[0, 1], [2, 3], [4]]
assert paginate([], 1) == [] and paginate([1, 2], 2) == [[1, 2]]
for value in [0, -1, True, 1.5]:
    raises(ValueError, paginate, [], value)
record = {'TOKEN': {'nested': 'secret'}, 'items': [{'Api_Key': 'hidden', 'name': 'ok'}]}
original = copy.deepcopy(record)
assert redact(record) == {'TOKEN': '[REDACTED]', 'items': [{'Api_Key': '[REDACTED]', 'name': 'ok'}]}
assert record == original
values = ['plain', 'a,b', 'say "hi"', 'two\nlines', '', 'carriage\rreturn']
output = csv_row(values)
assert output.endswith('\r\n') and list(csv.reader(io.StringIO(output, newline=''))) == [values]
assert csv_row([]) == '\r\n'
""",
    },
}


def oracle_source(case):
    return (
        "import sys\nfrom pathlib import Path\n"
        "root = Path.cwd().resolve()\nsys.path.insert(0, str(root))\n"
        "def raises(kind, fn, *args):\n"
        "    try: fn(*args)\n"
        "    except kind: return\n"
        "    raise AssertionError('Expected ' + kind.__name__)\n"
        + CASES[case]["oracle"]
    )
