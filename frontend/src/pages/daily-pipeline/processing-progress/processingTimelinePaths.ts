export interface TimelinePoint {
  x: number
  y: number
  rowTop: number
  rowBottom: number
}

const DOT_CLEARANCE = 7
const EDGE_GUTTER = 4
const TURN_RADIUS = 6
const ROW_TOLERANCE = 1
const coordinate = (value: number) => Math.round(value * 10) / 10

export function processingTimelinePaths(points: TimelinePoint[], width: number): string[] {
  return points.slice(1).map((next, index) => {
    const previous = points[index]
    const startX = coordinate(previous.x + DOT_CLEARANCE)
    const endX = coordinate(next.x - DOT_CLEARANCE)
    const startY = coordinate(previous.y)
    const endY = coordinate(next.y)
    if (Math.abs(previous.y - next.y) < ROW_TOLERANCE) return `M ${startX} ${startY} H ${endX}`
    const right = coordinate(width - EDGE_GUTTER)
    const left = EDGE_GUTTER
    const middle = coordinate((previous.rowBottom + next.rowTop) / 2)
    const radius = TURN_RADIUS
    return `M ${startX} ${startY} H ${right - radius} Q ${right} ${startY} ${right} ${startY + radius} V ${middle - radius} Q ${right} ${middle} ${right - radius} ${middle} H ${left + radius} Q ${left} ${middle} ${left} ${middle + radius} V ${endY - radius} Q ${left} ${endY} ${left + radius} ${endY} H ${endX}`
  })
}
