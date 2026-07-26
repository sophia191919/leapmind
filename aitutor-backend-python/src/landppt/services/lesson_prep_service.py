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
from ..ab_testing import ABExperiment
from .parallel_engine import ParallelExecutor
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

    # Minimum number of items to trigger parallel mode
    PARALLEL_THRESHOLD = 3

    def __init__(self, parallel: bool = False, experiment: ABExperiment | None = None):
        self.provider = get_ai_provider()
        self.parallel = parallel
        self.experiment = experiment
        self._pipeline: ValidationPipeline | None = None
        self._executor: ParallelExecutor | None = None

    @property
    def pipeline(self) -> ValidationPipeline:
        """Lazy-init validation pipeline (avoids import overhead on every request)."""
        if self._pipeline is None:
            self._pipeline = ValidationPipeline(auto_fix=True)
        return self._pipeline

    @property
    def executor(self) -> ParallelExecutor:
        """Lazy-init parallel executor (5 concurrent by default)."""
        if self._executor is None:
            self._executor = ParallelExecutor(max_concurrency=5)
        return self._executor

    # ════════════════════════════════════════════════════════════
    # [Layer 3] A/B test hooks
    # ════════════════════════════════════════════════════════════

    def _select_prompt_version(self, stage: str, user_id: int) -> str | None:
        """Select prompt version based on A/B experiment assignment.

        Returns:
            "A" for control, "B" for variant, None if no experiment is running.
        """
        if not self.experiment or self.experiment.status != "running":
            return None
        return self.experiment.assign(user_id)

    def _record_ab_metrics(self, ctx: PrepContext) -> None:
        """Record A/B test metrics after all stages complete."""
        if not self.experiment or self.experiment.status != "running":
            return

        group = self._select_prompt_version("final", ctx.user_id)
        if group is None:
            return

        # Calculate aggregate metrics from validation results
        slides = ctx.slides or []
        narrations = ctx.narrations or []

        schema_pass_rate = 1.0
        if slides:
            fallback = sum(1 for s in slides if s.get("is_fallback"))
            schema_pass_rate = 1.0 - (fallback / len(slides))

        orality_scores = []
        consistency_scores = []
        duration_deviations = []

        for n in narrations:
            val = n.get("_validation", {})
            if val:
                orality_scores.append(val.get("orality", {}).get("score", 0))
                consistency_scores.append(val.get("consistency", {}).get("coverage", 0))
                dur = val.get("duration", {})
                if dur.get("target_seconds", 0) > 0:
                    duration_deviations.append(dur.get("deviation", 0))

        avg_orality = sum(orality_scores) / max(len(orality_scores), 1)
        avg_consistency = sum(consistency_scores) / max(len(consistency_scores), 1)
        avg_dur_dev = sum(duration_deviations) / max(len(duration_deviations), 1)

        self.experiment.record_result(
            user_id=ctx.user_id,
            group=group,
            metrics={
                "schema_pass_rate": schema_pass_rate,
                "avg_orality_score": avg_orality,
                "avg_consistency_score": avg_consistency,
                "avg_duration_deviation": avg_dur_dev,
            },
        )

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

    async def _generate_single_slide(
        self, section: dict, ctx: PrepContext
    ) -> list[dict]:
        """Generate one or more slides from a syllabus section.

        Returns:
            List of slide dicts (may contain fallback placeholders).
        """
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
            logger.warning(f"Slide generation failed for section '{section.get('title', '')}': {e}")
            ctx.errors.append({
                "stage": "stage2",
                "section_title": section.get("title", ""),
                "error": str(e),
            })
            # Return one fallback slide
            return [{
                "page_num": 0,
                "type": "content",
                "title": section.get("title", "内容页"),
                "bullet_points": ["内容生成失败，请重试"],
                "is_fallback": True,
            }]

        slides_batch = parsed if isinstance(parsed, list) else [parsed]
        validated: list[dict] = []
        for slide in slides_batch:
            if not isinstance(slide, dict) or "title" not in slide:
                slide = {
                    "page_num": 0,
                    "type": "content",
                    "title": section.get("title", "内容页"),
                    "bullet_points": ["内容生成失败，请重试"],
                    "is_fallback": True,
                }
            # Validate and fix
            slide_vr = await self.pipeline.validate("slide", slide)
            if slide_vr.warnings:
                ctx.warnings.append({
                    "stage": "stage2",
                    "section_title": section.get("title", ""),
                    "warnings": slide_vr.warnings,
                })
            if slide_vr.fixes_applied:
                slide = self.pipeline.get_fixed_data("slide", slide)
            validated.append(slide)
        return validated

    async def _stage2_slides(self, ctx: PrepContext) -> AsyncGenerator[str, None]:
        """Generate slides: one AI call per syllabus section, yields per-slide events."""
        sections = ctx.syllabus.get("sections", [])

        if self.parallel and len(sections) >= self.PARALLEL_THRESHOLD:
            # ── Parallel path [Layer 3] ──
            slide_batches = await self.executor.map(
                items=sections,
                fn=lambda section: self._generate_single_slide(section, ctx),
                progress_callback=None,  # we yield after collection
            )
            all_slides: list[dict] = []
            for batch in slide_batches:
                if batch is None:
                    continue
                for slide in batch:
                    slide.setdefault("page_num", len(all_slides) + 1)
                    all_slides.append(slide)
                    yield _sse_event("slide", {
                        "page_num": slide["page_num"],
                        "total_pages": "?",
                        "slide": slide,
                    })
        else:
            # ── Serial path (original) ──
            all_slides = []
            for section in sections:
                slides_batch = await self._generate_single_slide(section, ctx)
                for slide in slides_batch:
                    slide.setdefault("page_num", len(all_slides) + 1)
                    all_slides.append(slide)
                    yield _sse_event("slide", {
                        "page_num": slide["page_num"],
                        "total_pages": "?",
                        "slide": slide,
                    })

        ctx.slides = all_slides
        yield _sse_event("slides_done", {
            "total_pages": len(all_slides),
        })

    # ════════════════════════════════════════════════════════════
    # Stage 3: Narration generation (per-slide)
    # ════════════════════════════════════════════════════════════

    async def _generate_single_narration(
        self, slide: dict, target_seconds: int, ctx: PrepContext
    ) -> dict:
        """Generate corrected narration for a single slide.

        Returns:
            Narration dict with _validation metadata (may be fallback on error).
        """
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
            logger.warning(f"Narration failed for slide {slide.get('page_num', '?')}: {e}")
            ctx.errors.append({
                "stage": "stage3",
                "page_num": slide.get("page_num", 0),
                "error": str(e),
            })
            narration = {
                "narration_text": f"（第{slide.get('page_num', '?')}页讲解词生成失败）",
                "estimated_duration_seconds": 30,
            }

        narration.setdefault("narration_text", "")
        narration.setdefault("estimated_duration_seconds", target_seconds)
        narration.setdefault("key_emphasis", [])
        narration.setdefault("pauses", [])

        # Validate + correct (Layer 2)
        corrected = self.pipeline.get_fixed_data(
            "narration", narration,
            slide=slide,
            target_seconds=target_seconds,
        )
        return corrected

    async def _stage3_narrations(self, ctx: PrepContext) -> AsyncGenerator[str, None]:
        """Generate narration text: one AI call per slide."""
        target_seconds_list = self._compute_target_seconds(ctx)
        slides = ctx.slides
        slide_count = len(slides)

        # Build task inputs: (slide, target_seconds)
        class NarrationTask:
            def __init__(self, slide: dict, target: int):
                self.slide = slide
                self.target_seconds = target

        tasks = [
            NarrationTask(slide, target_seconds_list[i] if i < len(target_seconds_list) else 60)
            for i, slide in enumerate(slides)
        ]

        if self.parallel and len(tasks) >= self.PARALLEL_THRESHOLD:
            # ── Parallel path [Layer 3] ──
            raw_narrations = await self.executor.map(
                items=tasks,
                fn=lambda t: self._generate_single_narration(t.slide, t.target_seconds, ctx),
                progress_callback=None,
            )
            all_narrations = [
                n if n is not None else {
                    "narration_text": "（讲解词生成失败）",
                    "estimated_duration_seconds": 30,
                }
                for n in raw_narrations
            ]
        else:
            # ── Serial path (original) ──
            all_narrations = []
            for i, task in enumerate(tasks):
                narration = await self._generate_single_narration(
                    task.slide, task.target_seconds, ctx
                )
                all_narrations.append(narration)

        # Yield narration events (and warnings) from collected results
        for i, (narration, task) in enumerate(zip(all_narrations, tasks)):
            page_num = task.slide.get("page_num", i + 1)
            val_score = narration.get("_validation", {}).get("overall_score", 1.0)

            # Check for warnings (re-run validation to get warn text)
            nar_vr = await self.pipeline.validate(
                "narration", narration.get("narration_text", ""),
                slide=task.slide,
                target_seconds=task.target_seconds,
            )
            if nar_vr.warnings:
                ctx.warnings.append({
                    "stage": "stage3",
                    "page_num": page_num,
                    "warnings": nar_vr.warnings,
                })
                for w in nar_vr.warnings:
                    yield _sse_event("warn", {
                        "stage": "stage3",
                        "validator": "narration_quality",
                        "page_num": page_num,
                        "message": w,
                    })

            yield _sse_event("narration", {
                "page_num": page_num,
                "total_pages": slide_count,
                "narration_text": narration["narration_text"],
                "estimated_duration_seconds": narration["estimated_duration_seconds"],
                "quality_score": val_score,
                "duration_deviation": narration.get("duration_deviation"),
                "needs_review": narration.get("_needs_review", False),
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
