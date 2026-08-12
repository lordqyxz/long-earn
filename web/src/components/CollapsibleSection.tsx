import { useState, type ReactNode } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'

interface Props {
  title: ReactNode
  children: ReactNode
  /** 初始是否展开，默认 true */
  defaultOpen?: boolean
  /** 头部右侧额外内容（不受折叠影响） */
  headerExtra?: ReactNode
  className?: string
  contentClassName?: string
}

export function CollapsibleSection({
  title,
  children,
  defaultOpen = true,
  headerExtra,
  className,
  contentClassName,
}: Props) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <Card className={className}>
      <CardHeader className="flex flex-row items-center justify-between py-3 cursor-pointer select-none" onClick={() => setOpen(!open)}>
        <CardTitle className="flex items-center gap-1.5 text-sm">
          {open ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
          {title}
        </CardTitle>
        {headerExtra && <div onClick={(e) => e.stopPropagation()}>{headerExtra}</div>}
      </CardHeader>
      {open && <CardContent className={contentClassName}>{children}</CardContent>}
    </Card>
  )
}
