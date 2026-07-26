import { request } from './api';
import {
  clearAuth,
  getUserInfo as getCachedUserInfo,
  saveToken,
  saveUserInfo,
} from '../utils/tokenManager';

function unwrap(payload) {
  return payload?.data ?? payload?.result ?? payload;
}

export async function login(credentials) {
  const payload = unwrap(await request('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify(credentials),
  }));

  if (payload?.token) saveToken(payload.token);
  if (payload?.user) saveUserInfo(payload.user);
  return payload;
}

export async function register(params) {
  return unwrap(await request('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify(params),
  }));
}

export async function getUserProfile() {
  try {
    const payload = unwrap(await request('/api/user/profile'));
    const userInfo = payload?.user ?? payload;
    if (userInfo) saveUserInfo(userInfo);
    return userInfo;
  } catch {
    return getCachedUserInfo();
  }
}

export async function updateUserProfile(profile) {
  const payload = unwrap(await request('/api/user/profile', {
    method: 'PUT',
    body: JSON.stringify(profile),
  }));
  if (payload) saveUserInfo(payload);
  return payload;
}

export function logout() {
  clearAuth();
}

export function checkAuth() {
  return Boolean(getCachedUserInfo());
}

