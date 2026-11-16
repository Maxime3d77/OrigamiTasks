"""Regression guards for the credential diagnostic modal.

Global form styles intentionally set input width:100%, so the wizard must
explicitly constrain its checkboxes or a single row causes horizontal overflow.
"""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/styles.css").read_text(encoding="utf-8")
JS = (ROOT / "static/app.js").read_text(encoding="utf-8")


def css_rule(selector):
    # Prefer the most recently defined rule for specificity-safe overrides.
    matches = re.findall(re.escape(selector) + r"\s*\{([^{}]+)\}", CSS, flags=re.S)
    assert matches, f"Missing CSS for {selector}"
    return matches[-1]


def test_wizard_checkbox_cannot_stretch_with_global_input_width():
    assert re.search(r"input,textarea,select\s*\{[^}]*width:100%", CSS)
    label = css_rule(".test-wizard-option")
    assert "grid-template-columns:18px minmax(0,1fr)" in label
    checkbox = css_rule(".test-wizard-option input[type=checkbox]")
    assert "width:18px" in checkbox
    assert "min-width:18px" in checkbox
    assert "max-width:18px" in checkbox
    assert "padding:0" in checkbox
    assert "appearance:auto" in checkbox
    text = css_rule(".test-wizard-option>span")
    assert "min-width:0" in text and "overflow-wrap:anywhere" in text


def test_wizard_modal_prevents_horizontal_scrolling():
    body = css_rule("#credentialTestLayer .modal-body")
    assert "overflow-x:hidden" in body and "overflow-y:auto" in body
    forms = css_rule("#credentialTestForm .form-grid")
    assert "repeat(2,minmax(0,1fr))" in forms
    mobile = re.search(
        r"@media\s*\(max-width:680px\)\s*\{(.+)",
        CSS,
        flags=re.S,
    )
    assert mobile and "grid-template-columns:minmax(0,1fr)" in mobile.group(1)
    for name in ("send_email", "check_sftp", "check_whoami", "check_directory",
                 "check_room", "send_message", "check_nodes"):
        assert "testOptionCheck('" + name + "'" in JS, name


def test_checkbox_is_a_real_checkbox_in_a_full_width_label():
    assert (
        """return '<label class="test-wizard-option"><input type="checkbox" name="'"""
        in JS
    )
