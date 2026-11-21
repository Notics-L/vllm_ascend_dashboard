import { Tag } from 'antd'
import type { TagProps } from 'antd'
import { getDailyCaseTagColor } from './dailyCaseTagColors'

interface DailyCaseTagProps extends Omit<TagProps, 'color' | 'children'> {
  label: string
}

function DailyCaseTag({ label, className, closable, onMouseDown, ...props }: DailyCaseTagProps) {
  return (
    <Tag
      {...props}
      className={`daily-case-business-tag${className ? ` ${className}` : ''}`}
      color={getDailyCaseTagColor(label)}
      closable={closable}
      onMouseDown={(event) => {
        if (closable) {
          event.preventDefault()
          event.stopPropagation()
        }
        onMouseDown?.(event)
      }}
    >
      {label}
    </Tag>
  )
}

export default DailyCaseTag
