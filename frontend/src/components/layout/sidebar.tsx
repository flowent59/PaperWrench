import {
  BarChart3,
  Copy,
  FileText,
  FolderTree,
  Filter,
  History,
  LayoutDashboard,
  ListChecks,
  ScanText,
  Settings,
  ShieldCheck,
  Wand2,
  Wrench,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { NavLink } from 'react-router-dom'

import { messages } from '@/i18n/messages'
import { cn } from '@/lib/utils'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  /** Milestone that delivers the screen; shown as a dimmed hint until then. */
  available: boolean
}

interface NavSection {
  title: string
  items: NavItem[]
}

/**
 * Navigation mirrors the information architecture of the brief (section 19).
 * Screens delivered by later milestones are listed but disabled, so the shape
 * of the product is visible from M0 without pretending features exist.
 */
export const navSections: NavSection[] = [
  {
    title: messages.nav.sectionWorkspace,
    items: [
      { to: '/', label: messages.nav.dashboard, icon: LayoutDashboard, available: true },
      { to: '/documents', label: messages.nav.documents, icon: FileText, available: true },
      { to: '/filters', label: messages.nav.filters, icon: Filter, available: false },
      { to: '/collections', label: messages.nav.collections, icon: FolderTree, available: false },
    ],
  },
  {
    title: messages.nav.sectionTools,
    items: [
      { to: '/transformations', label: messages.nav.transformations, icon: Wand2, available: true },
      { to: '/quality', label: messages.nav.quality, icon: ShieldCheck, available: false },
      { to: '/duplicates', label: messages.nav.duplicates, icon: Copy, available: false },
      { to: '/extraction', label: messages.nav.extraction, icon: ScanText, available: false },
      { to: '/schemas', label: messages.nav.schemas, icon: ListChecks, available: true },
      { to: '/analytics', label: messages.nav.analytics, icon: BarChart3, available: false },
    ],
  },
  {
    title: messages.nav.sectionOperations,
    items: [
      { to: '/jobs', label: messages.nav.jobs, icon: Wrench, available: true },
      { to: '/history', label: messages.nav.history, icon: History, available: true },
    ],
  },
  {
    title: messages.nav.sectionSystem,
    items: [
      { to: '/settings', label: messages.nav.settings, icon: Settings, available: false },
    ],
  },
]

export function Sidebar() {
  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r border-border bg-card md:flex">
      <div className="flex h-14 items-center gap-2 border-b border-border px-4">
        <Wrench className="h-5 w-5 text-primary" aria-hidden="true" />
        <div className="leading-tight">
          <div className="text-sm font-semibold">{messages.app.name}</div>
          <div className="text-[10px] text-muted-foreground">
            {messages.app.tagline}
          </div>
        </div>
      </div>

      <nav className="flex-1 space-y-5 overflow-y-auto p-3" aria-label={messages.nav.dashboard}>
        {navSections.map((section) => (
          <div key={section.title}>
            <div className="px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              {section.title}
            </div>
            <ul className="space-y-0.5">
              {section.items.map((item) => (
                <li key={item.to}>
                  {item.available ? (
                    <NavLink
                      to={item.to}
                      end={item.to === '/'}
                      className={({ isActive }) =>
                        cn(
                          'focus-ring flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors',
                          isActive
                            ? 'bg-primary/10 font-medium text-primary'
                            : 'text-foreground/80 hover:bg-accent hover:text-accent-foreground',
                        )
                      }
                    >
                      <item.icon className="h-4 w-4" aria-hidden="true" />
                      {item.label}
                    </NavLink>
                  ) : (
                    <span
                      className="flex cursor-not-allowed items-center gap-2.5 rounded-md px-2 py-1.5 text-sm text-muted-foreground/50"
                      title={messages.placeholder.milestone}
                      aria-disabled="true"
                    >
                      <item.icon className="h-4 w-4" aria-hidden="true" />
                      {item.label}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      <div className="border-t border-border p-3 text-[10px] leading-snug text-muted-foreground">
        {messages.app.disclaimer}
      </div>
    </aside>
  )
}
