import { request } from './api';

const FALLBACK_STAGES = [
  { stageCode: 'primary', stageName: '小学' },
  { stageCode: 'junior', stageName: '初中' },
  { stageCode: 'senior', stageName: '高中' },
];

const FALLBACK_GRADES = {
  primary: [
    { gradeCode: 'grade_1', gradeName: '一年级' },
    { gradeCode: 'grade_2', gradeName: '二年级' },
    { gradeCode: 'grade_3', gradeName: '三年级' },
    { gradeCode: 'grade_4', gradeName: '四年级' },
    { gradeCode: 'grade_5', gradeName: '五年级' },
    { gradeCode: 'grade_6', gradeName: '六年级' },
  ],
  junior: [
    { gradeCode: 'grade_7', gradeName: '七年级' },
    { gradeCode: 'grade_8', gradeName: '八年级' },
    { gradeCode: 'grade_9', gradeName: '九年级' },
  ],
  senior: [
    { gradeCode: 'grade_10', gradeName: '高一' },
    { gradeCode: 'grade_11', gradeName: '高二' },
    { gradeCode: 'grade_12', gradeName: '高三' },
  ],
};

function unwrapList(payload, fallback) {
  const value = payload?.data ?? payload?.result ?? payload;
  return Array.isArray(value) ? value : fallback;
}

export async function getAllStages() {
  try {
    return unwrapList(await request('/api/education/stages'), FALLBACK_STAGES);
  } catch {
    return FALLBACK_STAGES;
  }
}

export async function getGradesByStage(stageCode) {
  const fallback = FALLBACK_GRADES[stageCode] || FALLBACK_GRADES.primary;
  try {
    return unwrapList(await request(`/api/education/stages/${stageCode}/grades`), fallback);
  } catch {
    return fallback;
  }
}

