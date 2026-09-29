'use client'

import { useEffect } from 'react'
import { usePathname, useSearchParams } from 'next/navigation'

// When otii shows Otii Learn inside its own page, tell otii which page the
// person is on so otii's address can follow (refresh, back, shared links).
//
// Only the path is sent. Target '*' is safe here because only the sites named
// in OTII_LEARN_FRAME_ANCESTORS may frame this app at all (otii/framing.ts);
// no other site can be the parent that receives this.
export default function FrameBridge() {
  const pathname = usePathname()
  const searchParams = useSearchParams()

  useEffect(() => {
    if (typeof window === 'undefined' || window.parent === window) return
    const query = searchParams?.toString()
    const path = query ? `${pathname}?${query}` : pathname
    window.parent.postMessage({ type: 'otii-learn:location', path }, '*')
  }, [pathname, searchParams])

  return null
}
