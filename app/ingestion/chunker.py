from __future__ import annotations

from app.ingestion.tagging import derive_section_tags
from app.models.records import ChunkRecord, ExtractedDocument


def chunk_document(document: ExtractedDocument, max_chars: int = 900) -> list[ChunkRecord]:
    chunks: list[ChunkRecord] = []
    chunk_order = 0

    for section in document.sections:
        paragraphs = [item.strip() for item in section.text.split("\n") if item.strip()]
        if not paragraphs:
            continue

        buffer: list[str] = []
        current_length = 0

        def flush() -> None:
            nonlocal chunk_order, buffer, current_length
            if not buffer:
                return
            content = "\n".join(buffer).strip()
            section_tags = derive_section_tags(document.source, section.title, content)
            chunks.append(
                ChunkRecord(
                    chunk_id=f"{document.source.doc_id}-chunk-{chunk_order:04d}",
                    doc_id=document.source.doc_id,
                    title=document.source.title,
                    content=content,
                    order=chunk_order,
                    system_name=document.source.system_name,
                    source_type=document.source.source_type,
                    source_group=document.source.source_group,
                    source_file=document.source.file_path,
                    section_id=section.section_id,
                    section_title=section.title,
                    date_start=document.source.date_start,
                    date_end=document.source.date_end,
                    tags=section_tags,
                    section_path=section.section_path,
                    page_hint=section.page_hint,
                )
            )
            chunk_order += 1
            buffer = []
            current_length = 0

        for paragraph in paragraphs:
            paragraph_length = len(paragraph)
            if current_length and current_length + paragraph_length > max_chars:
                flush()
            buffer.append(paragraph)
            current_length += paragraph_length
        flush()

    return chunks
