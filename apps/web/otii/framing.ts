import type { NextResponse } from 'next/server'

// Which sites may show Otii Learn inside a frame (otii embeds it on its /learn
// page). Read at runtime so one build serves every environment.
//
// OTII_LEARN_FRAME_ANCESTORS: space-separated origins, e.g.
//   "https://app.get-otii.com https://staging.get-otii.com"
// Unset keeps LearnHouse's default: no framing at all.
export function applyFramingPolicy(response: NextResponse): NextResponse {
  const ancestors = (process.env.OTII_LEARN_FRAME_ANCESTORS || '')
    .split(/\s+/)
    .filter((origin) => /^https?:\/\/[^\s/]+$/.test(origin))

  if (ancestors.length === 0) {
    response.headers.set('X-Frame-Options', 'DENY')
    response.headers.set('Content-Security-Policy', "frame-ancestors 'none'")
    return response
  }

  // X-Frame-Options cannot name other sites; frame-ancestors supersedes it in
  // every current browser, so drop it rather than contradict the allow-list.
  response.headers.delete('X-Frame-Options')
  response.headers.set('Content-Security-Policy', `frame-ancestors 'self' ${ancestors.join(' ')}`)
  return response
}
