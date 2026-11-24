import pytest


@pytest.fixture
def environment_record():
    from pfc.environment import Environment

    return Environment(
        python="3.12.3",
        z3="4.16.0",
        clang="clang version 19.1.1",
        clang_target="x86_64-pc-linux-gnu",
        cpu="Example CPU",
        system="Linux x86_64",
        c_flags=("-std=c11", "-O2", "-Wall", "-Wextra", "-Werror"),
    )
