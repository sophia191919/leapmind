"""
SSE streaming API routes for M5 AI备课模块.

Single endpoint:
  POST /api/lesson-prep/generate → SSE stream (text/event-stream)
"""
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..services.lesson_prep_service import LessonPrepService

router = APIRouter(prefix="/api/lesson-prep", tags=["Lesson Preparation"])


# ─── Request model ───

class LessonPrepRequest(BaseModel):
    """Request body for lesson preparation generation."""
    user_id: int = Field(..., description="用户ID")
    title: str = Field(..., description="备课标题")
    subject: str = Field(..., description="科目: math/chinese/english/physics/chemistry/biology")
    grade: str = Field(..., description="年级: grade_7 ~ grade_12")
    knowledge_point_ids: list[int] = Field(..., description="知识点ID列表")
    teaching_goals: list[str] = Field(default_factory=list, description="教学目标列表")
    total_hours: int = Field(default=1, ge=1, le=10, description="课时数")
    style: str = Field(default="standard", description="备课风格: standard/detailed/interactive")
    weak_point_ids: list[int] = Field(default_factory=list, description="薄弱知识点ID列表（可选）")
    user_profile_summary: Optional[str] = Field(default=None, description="用户画像摘要（M6注入，可选）")


# ─── SSE streaming endpoint ───

@router.post("/generate")
async def generate_lesson_prep(request: LessonPrepRequest):
    """AI备课生成接口。

    返回 SSE (text/event-stream) 流，包含三个阶段的事件：
    1. syllabus_chunk / syllabus_done — 教学大纲
    2. slide / slides_done — PPT逐页结构
    3. narration — 口语化讲解词
    4. done — 全部完成（含prep_id）

    异常时返回:
      error — 包含 stage 和 message 字段
    """
    service = LessonPrepService()
    return StreamingResponse(
        service.run_stream(
            user_id=request.user_id,
            title=request.title,
            subject=request.subject,
            grade=request.grade,
            knowledge_point_ids=request.knowledge_point_ids,
            teaching_goals=request.teaching_goals,
            total_hours=request.total_hours,
            style=request.style,
            weak_point_ids=request.weak_point_ids,
            user_profile_summary=request.user_profile_summary,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
