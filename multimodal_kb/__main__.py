"""`python -m multimodal_kb` 入口（等同 `kb` 命令）。"""
from .cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
