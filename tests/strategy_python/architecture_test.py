"""把 strategy_python/README.md 中可机械检查的依赖约束固化为测试。"""

import ast
from pathlib import Path

STRATEGY_ROOT = Path(__file__).resolve().parents[2] / "strategy_python"
SHARED_DIRS = {"_execution", "_indicator", "_model", "_risk"}


def python_sources() -> list[Path]:
    return [
        path
        for path in STRATEGY_ROOT.rglob("*.py")
        if not path.name.endswith("_test.py") and "__pycache__" not in path.parts
        and not path.is_relative_to(STRATEGY_ROOT / "_report" / "analysis")
    ]


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def test_shared_modules_do_not_depend_on_concrete_strategies() -> None:
    for directory in SHARED_DIRS:
        for path in (STRATEGY_ROOT / directory).rglob("*.py"):
            concrete_imports = {
                module
                for module in imported_modules(path)
                if module.startswith("strategy_python.")
                and module.split(".", maxsplit=2)[1] not in SHARED_DIRS | {"_strategy", "_config"}
            }
            assert not concrete_imports, f"{path} 不得依赖具体策略：{sorted(concrete_imports)}"


def test_signal_modules_do_not_access_execution_or_order_apis() -> None:
    for path in STRATEGY_ROOT.glob("*/signal.py"):
        modules = imported_modules(path)
        forbidden = {
            module
            for module in modules
            if module.startswith(
                (
                    "nautilus_trader.trading",
                    "nautilus_trader.model.orders",
                    "strategy_python._execution",
                ),
            )
            or module.endswith(".execution")
        }
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls_order_api = any(
            isinstance(node, ast.Attribute)
            and node.attr in {"submit_order", "modify_order", "cancel_order"}
            for node in ast.walk(tree)
        )
        assert not forbidden, f"{path} 的信号层越过执行边界：{sorted(forbidden)}"
        assert not calls_order_api, f"{path} 的信号层不得调用订单 API"


def test_strategy_and_execution_layers_keep_their_boundaries() -> None:
    for path in STRATEGY_ROOT.glob("*/strategy.py"):
        forbidden = {
            module
            for module in imported_modules(path)
            if module.startswith(
                (
                    "nautilus_trader.model.events",
                    "nautilus_trader.model.orders",
                ),
            )
        }
        assert not forbidden, f"{path} 应把订单事件和订单对象交给 execution 层"

    execution_paths = list(STRATEGY_ROOT.glob("*/execution.py"))
    execution_paths.extend((STRATEGY_ROOT / "_execution").rglob("*.py"))
    for path in execution_paths:
        forbidden = {
            module
            for module in imported_modules(path)
            if module.endswith((".features", ".indicators", ".signal"))
        }
        assert not forbidden, f"{path} 的执行层不得判断指标或信号：{sorted(forbidden)}"


def test_config_state_and_backtest_dependencies_are_one_way() -> None:
    for path in python_sources():
        modules = imported_modules(path)
        if path.name != "backtest.py":
            assert not any(module.startswith("strategy_python.") and module.endswith(".backtest") for module in modules), (
                f"{path} 不得依赖回测入口"
            )
        if path.name == "config.py":
            assert not any(module.endswith(".state") for module in modules), (
                f"{path} 的静态配置不得依赖运行状态"
            )
        if path.name == "state.py":
            assert not any(module.endswith(".config") for module in modules), (
                f"{path} 的运行状态不得保存配置职责"
            )


def test_concrete_strategies_do_not_import_each_other():
    names = {p.parent.name for p in STRATEGY_ROOT.glob("*/strategy.py")}
    for name in names:
        for path in (STRATEGY_ROOT / name).glob("*.py"):
            others = {
                module for module in imported_modules(path)
                if module.startswith("strategy_python.")
                and module.split(".")[1] in names - {name}
            }
            assert not others, f"{path} 不得依赖其他具体策略：{sorted(others)}"
