from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def infer_source_group(source_type: str) -> str:
    normalized = source_type.strip().lower()
    if normalized in {"internal_manual", "official_manual", "emergency_plan"}:
        return "manual"
    if normalized in {"weekly_report", "monthly_report"}:
        return "case"
    return "general"


@dataclass(slots=True)
class SourceDocument:
    doc_id: str
    title: str
    source_type: str
    source_group: str
    system_name: str
    file_type: str
    file_path: str
    date_start: str | None = None
    date_end: str | None = None
    tags: list[str] = field(default_factory=list)
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceDocument":
        source_type = str(payload["source_type"])
        return cls(
            doc_id=str(payload["doc_id"]),
            title=str(payload["title"]),
            source_type=source_type,
            source_group=str(payload.get("source_group") or infer_source_group(source_type)),
            system_name=str(payload["system_name"]),
            file_type=str(payload["file_type"]).lower(),
            file_path=str(payload["file_path"]),
            date_start=str(payload["date_start"]).strip() if payload.get("date_start") else None,
            date_end=str(payload["date_end"]).strip() if payload.get("date_end") else None,
            tags=[str(item).strip() for item in payload.get("tags", []) if str(item).strip()],
            notes=str(payload.get("notes", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExtractedSection:
    section_id: str
    title: str
    text: str
    order: int
    page_hint: str | None = None
    section_path: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExtractedDocument:
    source: SourceDocument
    sections: list[ExtractedSection]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.to_dict(),
            "sections": [section.to_dict() for section in self.sections],
        }


@dataclass(slots=True)
class ChunkRecord:
    chunk_id: str
    doc_id: str
    title: str
    content: str
    order: int
    system_name: str
    source_type: str
    source_group: str
    source_file: str
    section_id: str
    section_title: str
    date_start: str | None = None
    date_end: str | None = None
    tags: list[str] = field(default_factory=list)
    section_path: list[str] = field(default_factory=list)
    page_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
