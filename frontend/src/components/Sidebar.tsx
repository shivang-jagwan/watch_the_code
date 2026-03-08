import React from 'react'
import { NavLink } from 'react-router-dom'

type NavItem = {
  to: string
  label: string
  icon: React.ReactNode
}

function Icon({ children }: { children: React.ReactNode }) {
  return (
    <span className="h-10 w-10 flex items-center justify-center">
      {children}
    </span>
  )
}

const NAV: NavItem[] = [
  {
    to: '/dashboard',
    label: 'Dashboard',
    icon: (
      <Icon>
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M4 13h7V4H4v9Zm9 7h7V11h-7v9ZM4 20h7v-5H4v5Zm9-11h7V4h-7v5Z" />
        </svg>
      </Icon>
    ),
  },
    {
      to: '/programs',
      label: 'Programs',
      icon: (
        <Icon>
          <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
            <circle cx="12" cy="7" r="4" />
          </svg>
        </Icon>
      ),
    },
  {
    to: '/teachers',
    label: 'Teachers',
    icon: (
      <Icon>
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
          <circle cx="12" cy="7" r="4" />
        </svg>
      </Icon>
    ),
  },
  {
    to: '/subjects',
    label: 'Subjects',
    icon: (
      <Icon>
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
          <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z" />
        </svg>
      </Icon>
    ),
  },
  {
    to: '/sections',
    label: 'Sections',
    icon: (
      <Icon>
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M3 3h18v6H3V3Zm0 12h18v6H3v-6Zm0-6h18" />
        </svg>
      </Icon>
    ),
  },
  {
    to: '/rooms',
    label: 'Rooms',
    icon: (
      <Icon>
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M3 21V8a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v13" />
          <path d="M9 21V12h6v9" />
        </svg>
      </Icon>
    ),
  },
  {
    to: '/time-slots',
    label: 'Time Slots',
    icon: (
      <Icon>
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7v6l4 2" />
        </svg>
      </Icon>
    ),
  },
  {
    to: '/curriculum',
    label: 'Curriculum (Track Mapping)',
    icon: (
      <Icon>
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M3 6h18M7 12h10M5 18h14" />
        </svg>
      </Icon>
    ),
  },
  {
    to: '/elective-blocks',
    label: 'Elective Blocks',
    icon: (
      <Icon>
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M4 7h16" />
          <path d="M4 12h16" />
          <path d="M4 17h16" />
          <path d="M6 7v10" />
          <path d="M18 7v10" />
        </svg>
      </Icon>
    ),
  },
  {
    to: '/combined-classes',
    label: 'Combined Classes',
    icon: (
      <Icon>
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M7 7h10v10H7V7Z" />
          <path d="M4 4h6M14 4h6M4 20h6M14 20h6" />
        </svg>
      </Icon>
    ),
  },
  {
    to: '/special-allotments',
    label: 'Special Allotments',
    icon: (
      <Icon>
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="3" y="11" width="18" height="11" rx="2" />
          <path d="M7 11V7a5 5 0 0 1 10 0v4" />
        </svg>
      </Icon>
    ),
  },
  {
    to: '/generate',
    label: 'Generate Timetable',
    icon: (
      <Icon>
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 6V3m0 18v-3m9-6h-3M6 12H3" />
          <path d="M7.8 7.8 5.6 5.6m12.8 12.8-2.2-2.2m0-8.4 2.2-2.2M5.6 18.4l2.2-2.2" />
          <circle cx="12" cy="12" r="4" />
        </svg>
      </Icon>
    ),
  },
  {
    to: '/timetable',
    label: 'Section Timetable',
    icon: (
      <Icon>
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M3 4h18v18H3V4Z" />
          <path d="M3 10h18" />
          <path d="M8 4v18" />
          <path d="M16 4v18" />
        </svg>
      </Icon>
    ),
  },
  {
    to: '/conflicts',
    label: 'Conflicts & Errors',
    icon: (
      <Icon>
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 9v4" />
          <path d="M12 17h.01" />
          <path d="M10.3 3h3.4L22 21H2L10.3 3Z" />
        </svg>
      </Icon>
    ),
  },
  {
    to: '/year-config',
    label: 'Year Config',
    icon: (
      <Icon>
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 2a10 10 0 1 0 0 20A10 10 0 0 0 12 2Z" />
          <path d="M12 8v4l3 3" />
          <path d="M9.5 3.5A8 8 0 0 0 4.3 12" />
        </svg>
      </Icon>
    ),
  },
]

export function Sidebar({
  collapsed,
  mobileOpen,
  onCloseMobile,
}: {
  collapsed: boolean
  mobileOpen: boolean
  onCloseMobile: () => void
}) {
  const isCollapsed = collapsed

  return (
    <aside
      className={
        [
          'app-scroll-region fixed top-16 left-0 z-50 h-[calc(100vh-4rem)] border-r border-green-200 bg-emerald-50',
          'w-64 overflow-y-auto transition-all duration-300 ease-in-out',
          mobileOpen ? 'translate-x-0' : '-translate-x-full',
          'md:translate-x-0',
          isCollapsed ? 'md:w-20' : 'md:w-64',
        ].join(' ')
      }
    >
      <div className="space-y-1 p-2">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              onClick={onCloseMobile}
              className={({ isActive }) =>
                (
                  'group flex items-center gap-4 rounded-xl px-4 py-3 text-sm transition-all duration-300 ease-in-out ' +
                  (isCollapsed ? 'justify-center px-2' : 'justify-start') +
                  (isActive
                    ? ' bg-green-600 text-white shadow-sm'
                    : ' text-slate-800 hover:bg-green-200/50')
                )
              }
            >
              <span className={isCollapsed ? 'text-current' : 'text-green-700 group-[.active]:text-white'}>
                {item.icon}
              </span>

              <span
                className={
                  [
                    'min-w-0 truncate font-medium transition-all duration-300 ease-in-out',
                    isCollapsed ? 'max-w-0 opacity-0' : 'max-w-[200px] opacity-100',
                  ].join(' ')
                }
              >
                {item.label}
              </span>
            </NavLink>
          ))}
      </div>
    </aside>
  )
}
