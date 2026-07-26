import { request } from './api';

export const SEMESTER = {
  FIRST: 'FIRST',
  SECOND: 'SECOND',
};

const FALLBACK_SECTIONS = [
  {
    id: 1,
    title: '第一单元',
    sectionName: '第一单元',
    progress: 72,
    lessons: [
      { id: 101, title: '基础练习', name: '基础练习', completed: true },
      { id: 102, title: '综合提升', name: '综合提升', completed: false },
    ],
  },
  {
    id: 2,
    title: '第二单元',
    sectionName: '第二单元',
    progress: 35,
    lessons: [
      { id: 201, title: '课后巩固', name: '课后巩固', completed: false },
      { id: 202, title: '错题回练', name: '错题回练', completed: false },
    ],
  },
];

function unwrapList(payload) {
  const value = payload?.data ?? payload?.result ?? payload;
  return Array.isArray(value) ? value : FALLBACK_SECTIONS;
}

export async function getSections(params = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== null)
  ).toString();

  try {
    return unwrapList(await request(`/api/courses/sections${query ? `?${query}` : ''}`));
  } catch {
    return FALLBACK_SECTIONS;
  }
}

