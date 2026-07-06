from mini_agent.core.tools.calculator import calculator_tool

TOOL = calculator_tool()


def _calc(expr):
    return TOOL.handler({"expression": expr}, None)


def test_basic():
    assert _calc("12 * 8").data["result"] == 96
    assert _calc("(12 + 8) * 3").data["result"] == 60
    assert _calc("2 ** 10").data["result"] == 1024
    assert _calc("10 / 4").data["result"] == 2.5


def test_functions_and_constants():
    r = _calc("sqrt(16)")
    assert r.ok and r.data["result"] == 4
    r = _calc("floor(pi)")
    assert r.ok and r.data["result"] == 3


def test_reject_attribute_access():
    r = _calc("(1).__class__")
    assert not r.ok


def test_reject_unknown_name():
    r = _calc("__import__('os')")
    assert not r.ok


def test_reject_huge_power():
    r = _calc("10 ** 100000")
    assert not r.ok


def test_division_by_zero():
    r = _calc("1/0")
    assert not r.ok


def test_reject_names():
    assert not _calc("x + 1").ok
    assert not _calc("[i for i in range(3)]").ok
