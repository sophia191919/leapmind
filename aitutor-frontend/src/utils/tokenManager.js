const TOKEN_KEY = 'leapmind_token';
const USER_INFO_KEY = 'leapmind_user_info';

export function saveToken(token) {
  if (!token) return;
  localStorage.setItem(TOKEN_KEY, token);
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || '';
}

export function removeToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export function hasValidToken() {
  return Boolean(getToken());
}

export function saveUserInfo(userInfo) {
  if (!userInfo) return;
  localStorage.setItem(USER_INFO_KEY, JSON.stringify(userInfo));
}

export function getUserInfo() {
  const raw = localStorage.getItem(USER_INFO_KEY);
  if (!raw) {
    return {
      id: 1,
      nickname: 'LeapMind User',
      stage: 'primary',
      grade: 'grade_5',
    };
  }

  try {
    return JSON.parse(raw);
  } catch {
    localStorage.removeItem(USER_INFO_KEY);
    return null;
  }
}

export function clearAuth() {
  removeToken();
  localStorage.removeItem(USER_INFO_KEY);
}

export function inferStageCodeFromGrade(gradeCode = '') {
  const value = String(gradeCode).toLowerCase();
  if (value.includes('senior') || value.includes('high') || value.includes('高中')) return 'senior';
  if (value.includes('junior') || value.includes('middle') || value.includes('初中')) return 'junior';
  if (value.includes('primary') || value.includes('小学')) return 'primary';

  const number = Number(value.match(/\d+/)?.[0]);
  if (number >= 10) return 'senior';
  if (number >= 7) return 'junior';
  return 'primary';
}

