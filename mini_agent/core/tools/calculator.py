"""calculator: safe arithmetic via AST parse + recursive evaluation.

No bare eval. Only a whitelist of operators, functions and constants is
allowed; variable names, attribute access, subscripts, calls to non-whitelisted
names, lambdas and comprehensions are rejected.
"""
from __future__ import annotations

import ast
import math
import operator as op

from .base import ToolContext, ToolResult, ToolSpec

_SCHEMA = {
    "type": "object",
    "properties": {
        "expression": {
            "type": "string",
            "description": "要计算的数学表达式，例如 (12 + 8) * 3",
        }
    },
    "required": ["expression"],
}

_BIN_OPS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.FloorDiv: op.floordiv,
    ast.Mod: op.mod,
    ast.Pow: op.pow,
}
_UNARY_OPS = {ast.UAdd: op.pos, ast.USub: op.neg}

_FUNCS = {
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "floor": math.floor,
    "ceil": math.ceil,
    "abs": abs,
    "round": round,
    "pow": pow,
}
_CONSTS = {"pi": math.pi, "e": math.e}

_POW_LIMIT = 1e6


class _CalcError(Exception):
    pass


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise _CalcError("只允许数字常量")
        return float(node.value)
    if isinstance(node, ast.BinOp):
        fn = _BIN_OPS.get(type(node.op))
        if fn is None:
            raise _CalcError("不支持的运算符")
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if type(node.op) is ast.Pow:
            if abs(left) > _POW_LIMIT or abs(right) > _POW_LIMIT or abs(right) > 1e4:
                raise _CalcError("幂运算超出安全上限")
        return fn(left, right)
    if isinstance(node, ast.UnaryOp):
        fn = _UNARY_OPS.get(type(node.op))
        if fn is None:
            raise _CalcError("不支持的一元运算符")
        return fn(_evaluate(node.operand))
    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise _CalcError(f"未知名称: {node.id}")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise _CalcError("只允许调用白名单函数")
        fname = node.func.id
        if fname not in _FUNCS:
            raise _CalcError(f"未知函数: {fname}")
        if node.keywords:
            raise _CalcError("不支持关键字参数")
        args = [_evaluate(a) for a in node.args]
        return float(_FUNCS[fname](*args))
    raise _CalcError("不支持的表达式结构")


def _run(arguments: dict, ctx: ToolContext) -> ToolResult:
    expr = arguments.get("expression", "")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        return ToolResult(ok=False, content="", error=f"表达式解析失败: {e}")
    try:
        value = _evaluate(tree)
    except _CalcError as e:
        return ToolResult(ok=False, content="", error=str(e))
    except ZeroDivisionError:
        return ToolResult(ok=False, content="", error="除以零")
    except (ValueError, OverflowError) as e:
        return ToolResult(ok=False, content="", error=f"计算错误: {e}")
    if not math.isfinite(value):
        return ToolResult(ok=False, content="", error="结果非有限（inf/nan）")
    # present integers without trailing .0
    if value == int(value) and abs(value) < 1e15:
        pretty = str(int(value))
    else:
        pretty = repr(value)
    return ToolResult(ok=True, content=f"{expr} = {pretty}", data={"expression": expr, "result": value})


def calculator_tool() -> ToolSpec:
    return ToolSpec(
        name="calculator",
        description="基础数学计算器：加减乘除、括号、幂与常见数学函数（sqrt/log/sin 等）。",
        parameters_schema=_SCHEMA,
        handler=_run,
    )
