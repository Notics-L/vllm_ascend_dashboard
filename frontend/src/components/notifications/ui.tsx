import type { ReactNode } from 'react'

interface PanelHeaderProps {
  icon: ReactNode
  title: string
  description?: string
  actions?: ReactNode
}

export function PanelHeader({ icon, title, description, actions }: PanelHeaderProps) {
  return (
    <div className="nc-panel-header">
      <div>
        <h3 className="nc-panel-title">{icon}<span>{title}</span></h3>
        {description && <div className="nc-panel-desc">{description}</div>}
      </div>
      {actions && <div className="nc-panel-actions">{actions}</div>}
    </div>
  )
}

interface SectionCardProps {
  icon?: ReactNode
  title: string
  description?: string
  extra?: ReactNode
  children: ReactNode
  tone?: 'blue' | 'purple' | 'orange'
  wide?: boolean
}

export function SectionCard({ icon, title, description, extra, children, tone = 'blue', wide = false }: SectionCardProps) {
  return (
    <section className={`nc-section-card nc-tone-${tone}${wide ? ' nc-section-wide' : ''}`}>
      <div className="nc-section-head">
        <div className="nc-section-head-main">
          {icon && <span className="nc-section-icon">{icon}</span>}
          <div>
            <div className="nc-section-title">{title}</div>
            {description && <div className="nc-section-desc">{description}</div>}
          </div>
        </div>
        {extra && <div className="nc-section-extra">{extra}</div>}
      </div>
      <div className="nc-section-body">{children}</div>
    </section>
  )
}

interface ToggleRowProps {
  label: string
  description?: string
  control: ReactNode
}

export function ToggleRow({ label, description, control }: ToggleRowProps) {
  return (
    <div className="nc-toggle-row">
      <div className="nc-toggle-main">
        <div className="nc-toggle-label">{label}</div>
        {description && <div className="nc-toggle-desc">{description}</div>}
      </div>
      <div className="nc-toggle-control">{control}</div>
    </div>
  )
}
