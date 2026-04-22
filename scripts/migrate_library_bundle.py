from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.library_bundle import export_bundle, import_bundle


def main() -> None:
    parser = argparse.ArgumentParser(description="资料库迁移包导出/导入")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="导出迁移包")
    export_parser.add_argument("--output", required=True, help="输出 zip 文件路径")

    import_parser = subparsers.add_parser("import", help="导入迁移包")
    import_parser.add_argument("--input", required=True, help="输入 zip 文件路径")
    import_parser.add_argument("--replace", action="store_true", help="导入前清空现有资料库")

    args = parser.parse_args()
    if args.command == "export":
        path = export_bundle(Path(args.output).resolve())
        print(path)
        return
    if args.command == "import":
        summary = import_bundle(Path(args.input).resolve(), replace_existing=args.replace)
        print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
