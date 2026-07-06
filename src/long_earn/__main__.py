"""long_earn 包入口 —— 委托给统一 typer CLI。

注册为 console_scripts: ``long_earn = "long_earn.__main__:main"``
等价于直接执行 ``python -m long_earn``。

子命令: research / download / agent / web
详见 ``long-earn --help``。
"""

from long_earn.cli import app


def main() -> None:
    """主函数 —— 转发给 typer app。"""
    app()


if __name__ == "__main__":
    main()
