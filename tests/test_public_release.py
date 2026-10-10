import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("public_release", Path(__file__).parents[1] / "tools/check_public_release.py")
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


def test_secret_values_are_flagged_without_being_returned():
    value = "fake" + "credential_value_for_test"
    findings = scanner.inspect_text("password = " + repr(value), ".py")
    assert findings == [(1, "literal_credential")]
    assert value not in repr(findings)


def test_environment_credential_reference_is_allowed():
    assert scanner.inspect_text('password = os.environ["DATABASE_PASSWORD"]', ".py") == []


def test_json_credential_is_blocked():
    assert scanner.inspect_text('{"token": "' + 'fake_credential' + '"}', ".json") == [(1, "literal_credential")]


def test_new_public_system_has_no_detected_disclosures():
    assert scanner.scan()["passed"]
