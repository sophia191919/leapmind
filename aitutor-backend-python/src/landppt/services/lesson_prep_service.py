"""
Three-stage lesson preparation orchestration service for M5 AI备课.

Workflow:
  Stage 1 (streaming):  knowledge_points + goals → syllabus JSON
  Stage 2 (per-page):    syllabus → slide JSONs (one AI call per section, yields per-slide)
  Stage 3 (per-page):    slides → narration JSONs (one AI call per slide)

All stages share a PrepContext passed through memory. On completion, the
full result is written to the teaching_contents table.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional

from ..ai import get_ai_provider, AIMessage, MessageRole
from ..utils.json_extractor import JSONExtractor
from ..validators import ValidationPipeline
from .prompts.lesson_prep_prompts import (
    build_stage1_messages,
    build_stage2_messages,
    build_stage3_messages,
)

logger = logging.getLogger(__name__)


# ─── Context object (in-memory, passed between stages) ───

@dataclass
class PrepContext:
    user_id: int
    title: str
    subject: str
    grade: str
    knowledge_point_ids: list[int]
    teaching_goals: list[str]
    total_hours: int
    style: str
    weak_point_ids: list[int]
    user_profile_summary: Optional[str] = None

    # Stage outputs
    syllabus: dict = field(default_factory=dict)
    slides: list[dict] = field(default_factory=list)
    narrations: list[dict] = field(default_factory=list)

    # Error tracking
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


# ─── Event helpers ───

def _sse_event(event: str, data: dict) -> str:
    """Format a Server-Sent Event string."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ─── Main service ───

class LessonPrepService:
    """Three-stage lesson preparation service."""

    def __init__(self):
        self.provider = get_ai_provider()
        self._pipeline: ValidationPipeline | None = None

    @property
    def pipeline(self) -> ValidationPipeline:
        """Lazy-init validation pipeline (avoids import overhead on every request)."""
        if self._pipeline is None:
            self._pipeline = ValidationPipeline(auto_fix=True)
        return self._pipeline

    # ════════════════════════════════════════════════════════════
    # Public entry point
    # ════════════════════════════════════════════════════════════

    async def run_stream(
        self,
        user_id: int,
        title: str,
        subject: str,
        grade: str,
        knowledge_point_ids: list[int],
        teaching_goals: list[str],
        total_hours: int = 1,
        style: str = "standard",
        weak_point_ids: list[int] | None = None,
        user_profile_summary: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Main entry: run all three stages and emit SSE events."""
        ctx = PrepContext(
            user_id=user_id,
            title=title,
            subject=subject,
            grade=grade,
            knowledge_point_ids=knowledge_point_ids,
            teaching_goals=teaching_goals,
            total_hours=total_hours,
            style=style,
            weak_point_ids=weak_point_ids or [],
            user_profile_summary=user_profile_summary,
        )

        try:
            # Stage 1
            async for event in self._stage1_syllabus(ctx):
                yield event

            # Stage 2 (only if Stage 1 succeeded)
            if ctx.syllabus:
                async for event in self._stage2_slides(ctx):
                    yield event

            # Stage 3 (only if Stage 2 produced slides)
            if ctx.slides:
                async for event in self._stage3_narrations(ctx):
                    yield event

            # ── [Layer 2] Final combined validation ──
            if ctx.slides:
                final_vr = await self.pipeline.validate(
                    "final", {
                        "syllabus": ctx.syllabus,
                        "slides": ctx.slides,
                        "narrations": ctx.narrations,
                    },
                )
                if final_vr.warnings:
                    ctx.warnings.append({"stage": "final", "warnings": final_vr.warnings})
                    for w in final_vr.warnings:
                        yield _sse_event("warn", {
                            "stage": "final",
                            "validator": "final_check",
                            "message": w,
                        })
                yield _sse_event("final_check", {
                    "passed": final_vr.passed,
                    "overall_score": round(final_vr.overall_score, 3),
                    "warnings": final_vr.warnings,
                })

            # Database write
            if ctx.slides:
                try:
                    prep_id = await self._save_to_db(ctx)
                    total_seconds = sum(
                        n.get("estimated_duration_seconds", 0) or 0
                        for n in (ctx.narrations or [])
                    )
                    yield _sse_event("done", {
                        "prep_id": prep_id,
                        "total_pages": len(ctx.slides),
                        "total_duration_seconds": total_seconds,
                    })
                except Exception as e:
                    logger.exception("Database write failed")
                    yield _sse_event("error", {
                        "stage": "db",
                        "message": f"备课内容已生成但保存失败: {e}",
                    })

        except Exception as e:
            logger.exception("Lesson preparation failed")
            yield _sse_event("error", {
                "stage": "global",
                "message": str(e),
            })

    # ════════════════════════════════════════════════════════════
    # Stage 1: Syllabus generation (streaming)
    # ════════════════════════════════════════════════════════════

    async def _stage1_syllabus(self, ctx: PrepContext) -> AsyncGenerator[str, None]:
        """Generate syllabus with streaming tokens, then yield the full JSON."""
        messages = build_stage1_messages(
            subject=ctx.subject,
            grade=ctx.grade,
            knowledge_point_ids=ctx.knowledge_point_ids,
            teaching_goals=ctx.teaching_goals,
            total_hours=ctx.total_hours,
            style=ctx.style,
            weak_point_ids=ctx.weak_point_ids,
            user_profile_summary=ctx.user_profile_summary,
        )

        full_content = ""
        try:
            async for chunk in self.provider.stream_chat_completion(messages):
                full_content += chunk
                yield _sse_event("syllabus_chunk", {"chunk": chunk})
        except Exception as e:
            logger.exception("Stage 1 (syllabus) streaming failed")
            yield _sse_event("error", {"stage": "stage1", "message": f"大纲生成失败: {e}"})
            return

        # Parse syllabus JSON from the full streaming output
        try:
            syllabus = JSONExtractor.extract_dict(full_content)
        except Exception as e:
            logger.exception("Failed to parse syllabus JSON")
            yield _sse_event("error", {
                "stage": "stage1",
                "message": f"大纲JSON解析失败: {e}",
            })
            return

        # ── [Layer 2] Validate syllabus schema ──
        vr = await self.pipeline.validate("syllabus", syllabus)
        if vr.warnings:
            ctx.warnings.append({"stage": "stage1", "warnings": vr.warnings})
            for w in vr.warnings:
                yield _sse_event("warn", {
                    "stage": "stage1",
                    "validator": "syllabus_schema",
                    "message": w,
                })
        if vr.fixes_applied:
            syllabus = self.pipeline.get_fixed_data("syllabus", syllabus)
            ctx.warnings.append({"stage": "stage1", "fixes": vr.fixes_applied})

        # Basic validation
        sections = syllabus.get("sections", [])
        if not sections:
            yield _sse_event("error", {
                "stage": "stage1",
                "message": "生成的大纲中没有课时(sections)数据",
            })
            return

        ctx.syllabus = syllabus
        yield _sse_event("syllabus_done", {
            "syllabus": syllabus,
            "sections_count": len(sections),
        })

    # ════════════════════════════════════════════════════════════
    # Stage 2: PPT slide generation (per-section batch, per-slide yield)
    # ════════════════════════════════════════════════════════════

    async def _stage2_slides(self, ctx: PrepContext) -> AsyncGenerator[str, None]:
        """Generate slides: one AI call per syllabus section, yields per-slide events."""
        all_slides: list[dict] = []

        for section in ctx.syllabus.get("sections", []):
            section_json = json.dumps(section, ensure_ascii=False, indent=2)
            messages = build_stage2_messages(
                section_json=section_json,
                user_profile_summary=ctx.user_profile_summary,
            )

            try:
                response = await self.provider.chat_completion(messages)
                raw = response.content
                parsed = JSONExtractor.extract(raw, fallback=[])
            except Exception as e:
                logger.warning(f"Slide generation failed for section: {e}")
                ctx.errors.append({
                    "stage": "stage2",
                    "section_title": section.get("title", ""),
                    "error": str(e),
                })
                parsed = []

            # parsed could be a single slide dict or a list of slides
            slides_batch = parsed if isinstance(parsed, list) else [parsed]

            for slide in slides_batch:
                if not isinstance(slide, dict) or "title" not in slide:
                    # Fallback placeholder
                    slide = {
                        "page_num": len(all_slides) + 1,
                        "type": "content",
                        "title": section.get("title", "内容页"),
                        "bullet_points": ["内容生成失败，请重试"],
                        "is_fallback": True,
                    }

                # ── [Layer 2] Validate slide schema ──
                slide_vr = await self.pipeline.validate("slide", slide)
                if slide_vr.warnings:
                    ctx.warnings.append({
                        "stage": "stage2",
                        "page_num": slide.get("page_num", len(all_slides) + 1),
                        "warnings": slide_vr.warnings,
                    })
                    for w in slide_vr.warnings:
                        yield _sse_event("warn", {
                            "stage": "stage2",
                            "validator": "slide_schema",
                            "page_num": slide.get("page_num", len(all_slides) + 1),
                            "message": w,
                        })
                if slide_vr.fixes_applied:
                    slide = self.pipeline.get_fixed_data("slide", slide)

                slide.setdefault("page_num", len(all_slides) + 1)
                all_slides.append(slide)
                yield _sse_event("slide", {
                    "page_num": slide["page_num"],
                    "total_pages": "?",  # updated in slides_done
                    "slide": slide,
                })

        ctx.slides = all_slides
        yield _sse_event("slides_done", {
            "total_pages": len(all_slides),
        })

    # ════════════════════════════════════════════════════════════
    # Stage 3: Narration generation (per-slide)
    # ════════════════════════════════════════════════════════════

    async def _stage3_narrations(self, ctx: PrepContext) -> AsyncGenerator[str, None]:
        """Generate narration text: one AI call per slide."""
        all_narrations: list[dict] = []

        # Pre-compute target durations per slide
        target_seconds_list = self._compute_target_seconds(ctx)

        for idx, slide in enumerate(ctx.slides):
            target_seconds = target_seconds_list[idx] if idx < len(target_seconds_list) else 60
            slide_json = json.dumps(slide, ensure_ascii=False, indent=2)

            messages = build_stage3_messages(
                slide_json=slide_json,
                slide_title=slide.get("title", ""),
                slide_type=slide.get("type", "content"),
                target_seconds=target_seconds,
                subject=ctx.subject,
            )

            try:
                response = await self.provider.chat_completion(messages)
                raw = response.content
                narration = JSONExtractor.extract_dict(raw, fallback={})
            except Exception as e:
                logger.warning(f"Narration generation failed for slide {idx + 1}: {e}")
                ctx.errors.append({
                    "stage": "stage3",
                    "page_num": slide.get("page_num", idx + 1),
                    "error": str(e),
                })
                narration = {"narration_text": f"（第{idx + 1}页讲解词生成失败）", "estimated_duration_seconds": 30}

            # Ensure required fields
            narration.setdefault("narration_text", "")
            narration.setdefault("estimated_duration_seconds", target_seconds)
            narration.setdefault("key_emphasis", [])
            narration.setdefault("pauses", [])

            # ── [Layer 2] Validate + correct narration quality ──
            # Run get_fixed_data which validates and applies corrections:
            #   - Override estimated_duration_seconds with validator's calculation
            #   - Add _validation metadata block (scores, issues)
            #   - Flag _needs_review if oral quality is poor
            corrected_narration = self.pipeline.get_fixed_data(
                "narration", narration,
                slide=slide,
                target_seconds=target_seconds,
            )

            # Also collect warnings for SSE warn events
            nar_vr = await self.pipeline.validate(
                "narration", narration.get("narration_text", ""),
                slide=slide,
                target_seconds=target_seconds,
            )
            if nar_vr.warnings:
                ctx.warnings.append({
                    "stage": "stage3",
                    "page_num": slide.get("page_num", idx + 1),
                    "warnings": nar_vr.warnings,
                })
                for w in nar_vr.warnings:
                    yield _sse_event("warn", {
                        "stage": "stage3",
                        "validator": "narration_quality",
                        "page_num": slide.get("page_num", idx + 1),
                        "message": w,
                    })

            # Extract quality score for event metadata
            val_score = corrected_narration.get("_validation", {}).get("overall_score", 1.0)

            all_narrations.append(corrected_narration)
            yield _sse_event("narration", {
                "page_num": slide.get("page_num", idx + 1),
                "total_pages": len(ctx.slides),
                "narration_text": corrected_narration["narration_text"],
                "estimated_duration_seconds": corrected_narration["estimated_duration_seconds"],
                "quality_score": val_score,
                "duration_deviation": corrected_narration.get("duration_deviation"),
                "needs_review": corrected_narration.get("_needs_review", False),
            })

        ctx.narrations = all_narrations

    # ════════════════════════════════════════════════════════════
    # Duration estimation
    # ════════════════════════════════════════════════════════════

    def _compute_target_seconds(self, ctx: PrepContext) -> list[int]:
        """Estimate target narration seconds for each slide based on teaching process."""
        total_minutes = 0
        for section in ctx.syllabus.get("sections", []):
            for step in section.get("teaching_process", []):
                dur_str = step.get("duration", "5min")
                match = re.search(r"(\d+)", dur_str)
                if match:
                    total_minutes += int(match.group(1))

        if total_minutes <= 0:
            total_minutes = 45 * ctx.total_hours  # fallback

        num_slides = len(ctx.slides)
        if num_slides == 0:
            return []

        seconds_per_slide = (total_minutes * 60) / num_slides
        # Clamp to [30, 180] seconds per slide
        clamped = max(30, min(180, int(seconds_per_slide)))
        return [clamped] * num_slides

    # ════════════════════════════════════════════════════════════
    # Database persistence
    # ════════════════════════════════════════════════════════════

    async def _save_to_db(self, ctx: PrepContext) -> int:
        """Write the full preparation result to teaching_contents table."""
        from ..database.database import AsyncSessionLocal
        from ..database.models import TeachingContent

        async with AsyncSessionLocal() as session:
            record = TeachingContent(
                user_id=ctx.user_id,
                type="lesson_plan",
                title=ctx.title,
                source_type="from_weakpoint" if ctx.weak_point_ids else "from_text",
                source_content_json=json.dumps({
                    "title": ctx.title,
                    "subject": ctx.subject,
                    "grade": ctx.grade,
                    "knowledge_point_ids": ctx.knowledge_point_ids,
                    "teaching_goals": ctx.teaching_goals,
                    "total_hours": ctx.total_hours,
                    "style": ctx.style,
                    "weak_point_ids": ctx.weak_point_ids,
                    "user_profile_summary": ctx.user_profile_summary,
                }, ensure_ascii=False),
                generated_content_json=json.dumps({
                    "syllabus": ctx.syllabus,
                    "slides": ctx.slides,
                    "narrations": ctx.narrations,
                }, ensure_ascii=False),
                ppt_structure_json=json.dumps(ctx.slides, ensure_ascii=False),
                status="published",
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record.id
