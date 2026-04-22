from __future__ import annotations

import argparse
from pathlib import Path

from app.core.config import get_settings
from app.services.library_bundle import export_bundle, import_bundle
from app.services.library import build_library_analysis, export_analysis, export_documents, init_library_store, rebuild_all_documents, reindex_document


def main() -> None:
    parser = argparse.ArgumentParser(description='资料库管理脚本')
    subparsers = parser.add_subparsers(dest='command', required=True)

    subparsers.add_parser('init', help='初始化资料库')
    subparsers.add_parser('rebuild', help='全量重建资料索引')

    reindex_parser = subparsers.add_parser('reindex', help='重新索引单个文档')
    reindex_parser.add_argument('doc_id', help='文档 ID')

    export_parser = subparsers.add_parser('export', help='导出资料库元数据或分析结果')
    export_parser.add_argument('--kind', choices=['documents', 'analysis'], default='documents')
    export_parser.add_argument('--format', choices=['csv', 'json'], default='csv')
    export_parser.add_argument('--output', default='', help='输出文件路径')

    bundle_export_parser = subparsers.add_parser('bundle-export', help='导出资料库迁移包')
    bundle_export_parser.add_argument('--output', required=True, help='输出 zip 文件路径')

    bundle_import_parser = subparsers.add_parser('bundle-import', help='导入资料库迁移包')
    bundle_import_parser.add_argument('--input', required=True, help='输入 zip 文件路径')
    bundle_import_parser.add_argument('--replace', action='store_true', help='导入前清空现有资料库')

    subparsers.add_parser('analysis', help='打印资料库分析摘要')

    args = parser.parse_args()
    settings = get_settings()

    if args.command == 'init':
        init_library_store(settings)
        print('资料库初始化完成')
        return
    if args.command == 'rebuild':
        summary = rebuild_all_documents(settings)
        print(summary)
        return
    if args.command == 'reindex':
        result = reindex_document(args.doc_id, settings)
        print(result)
        return
    if args.command == 'analysis':
        print(build_library_analysis(settings))
        return
    if args.command == 'export':
        if args.kind == 'analysis':
            filename, _media_type, content = export_analysis(args.format, settings=settings)
        else:
            filename, _media_type, content = export_documents(args.format, settings=settings)
        output_path = Path(args.output) if args.output else settings.data_dir / 'exports' / filename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content)
        print(output_path)
        return
    if args.command == 'bundle-export':
        print(export_bundle(Path(args.output)))
        return
    if args.command == 'bundle-import':
        print(import_bundle(Path(args.input), replace_existing=args.replace))
        return


if __name__ == '__main__':
    main()
