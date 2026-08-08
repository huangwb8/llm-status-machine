from __future__ import annotations

import ast
import math
from collections.abc import Mapping
from typing import Any

ALLOWED_FUNCTIONS = {"sin": math.sin, "cos": math.cos, "exp": math.exp, "log": math.log}
ALLOWED_BINARY = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
    ast.Pow: lambda left, right: left**right,
}
ALLOWED_UNARY = {ast.UAdd: lambda value: value, ast.USub: lambda value: -value}
MAX_EXPRESSION_CHARS = 256
MAX_AST_NODES = 64
MAX_ABS_CONSTANT = 1_000_000
MAX_ABS_EXPONENT = 12


class ExpressionError(ValueError):
    """候选表达式不满足公开 grammar。"""


def parse_expression(expression: str) -> ast.Expression:
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise ExpressionError("expression is too long")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise ExpressionError(f"invalid syntax: {error.msg}") from error
    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        raise ExpressionError("expression contains too many syntax nodes")
    _validate(tree)
    return tree


def _validate(node: ast.AST) -> None:
    if isinstance(node, ast.Expression):
        _validate(node.body)
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ExpressionError("only numeric constants are allowed")
        if not math.isfinite(float(node.value)) or abs(float(node.value)) > MAX_ABS_CONSTANT:
            raise ExpressionError("numeric constant is outside the allowed range")
    elif isinstance(node, ast.Name):
        if node.id != "x":
            raise ExpressionError(f"unknown variable: {node.id}")
    elif isinstance(node, ast.BinOp):
        if type(node.op) not in ALLOWED_BINARY:
            raise ExpressionError(f"operator is not allowed: {type(node.op).__name__}")
        if isinstance(node.op, ast.Pow) and (
            not isinstance(node.right, ast.Constant)
            or isinstance(node.right.value, bool)
            or not isinstance(node.right.value, (int, float))
            or abs(float(node.right.value)) > MAX_ABS_EXPONENT
        ):
            raise ExpressionError("power exponent must be a bounded numeric constant")
        _validate(node.left)
        _validate(node.right)
    elif isinstance(node, ast.UnaryOp):
        if type(node.op) not in ALLOWED_UNARY:
            raise ExpressionError(f"unary operator is not allowed: {type(node.op).__name__}")
        _validate(node.operand)
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCTIONS:
            raise ExpressionError("function call is not allowed")
        if len(node.args) != 1 or node.keywords:
            raise ExpressionError("functions require exactly one positional argument")
        _validate(node.args[0])
    else:
        raise ExpressionError(f"syntax is not allowed: {type(node).__name__}")


def evaluate_expression(expression: str, x: float) -> float:
    try:
        value = _evaluate(parse_expression(expression).body, {"x": float(x)})
    except (ArithmeticError, ValueError) as error:
        raise ExpressionError(f"expression evaluation failed: {error}") from error
    if not math.isfinite(value):
        raise ExpressionError("expression returned a non-finite value")
    return value


def _evaluate(node: ast.AST, values: Mapping[str, float]) -> float:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, ast.BinOp):
        return ALLOWED_BINARY[type(node.op)](_evaluate(node.left, values), _evaluate(node.right, values))
    if isinstance(node, ast.UnaryOp):
        return ALLOWED_UNARY[type(node.op)](_evaluate(node.operand, values))
    if isinstance(node, ast.Call):
        return ALLOWED_FUNCTIONS[node.func.id](_evaluate(node.args[0], values))  # type: ignore[union-attr]
    raise AssertionError(f"validated node was not executable: {type(node).__name__}")


def normalized_mse(expression: str, observations: list[dict[str, Any]]) -> float:
    predictions = [evaluate_expression(expression, float(row["x"])) for row in observations]
    actual = [float(row["y"]) for row in observations]
    mse = sum((predicted - observed) ** 2 for predicted, observed in zip(predictions, actual, strict=True)) / len(actual)
    mean = sum(actual) / len(actual)
    variance = sum((value - mean) ** 2 for value in actual) / len(actual)
    return mse / max(variance, 1e-12)
