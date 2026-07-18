from typing import Literal

import pytest


@pytest.fixture
def example_fixture() -> Literal[1]:
    return 1


def test_example(example_fixture: Literal[1]) -> None:
    assert example_fixture == 1
