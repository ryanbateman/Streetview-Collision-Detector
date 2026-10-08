import pytest

from svcd.models import SOURCE_GOOGLE, SOURCE_USER, panoSource


@pytest.mark.parametrize("credit, expected", [
    (None, SOURCE_GOOGLE),
    ("", SOURCE_GOOGLE),
    ("  ", SOURCE_GOOGLE),
    ("© 2024 Google", SOURCE_GOOGLE),
    ("Google", SOURCE_GOOGLE),
    ("Imagery ©2024 GOOGLE", SOURCE_GOOGLE),
    ("© Jane Doe", SOURCE_USER),
    ("© Fixture Contributor 3", SOURCE_USER),
])
def test_pano_source_classifies_credit_lines(credit, expected):
    assert panoSource(credit) == expected
