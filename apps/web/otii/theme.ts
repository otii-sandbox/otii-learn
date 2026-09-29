import type { NextResponse } from 'next/server'

// Which look Otii Learn uses, read at runtime so one build serves every
// environment. OTII_LEARN_THEME=otii turns on otii/theme.css; unset keeps
// LearnHouse's own look. The value reaches the browser as a cookie that
// public/otii-theme.js reads before the first paint.
const COOKIE = 'otii_learn_theme'

export function applyThemeCookie(response: NextResponse): NextResponse {
  const theme = (process.env.OTII_LEARN_THEME || '').trim()
  if (/^[a-z0-9-]+$/.test(theme)) {
    response.cookies.set({ name: COOKIE, value: theme, path: '/', sameSite: 'lax' })
  } else {
    response.cookies.delete(COOKIE)
  }
  return response
}
