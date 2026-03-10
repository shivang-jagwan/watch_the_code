import * as React from 'react'
import * as Select from '@radix-ui/react-select'

export type PremiumSelectOption = {
  value: string
  label: string
  disabled?: boolean
}

type Props = {
  id?: string
  value?: string
  onValueChange: (value: string) => void
  options: PremiumSelectOption[]
  placeholder?: string
  disabled?: boolean
  searchable?: boolean
  searchMinOptions?: number
  searchPlaceholder?: string
  searchAriaLabel?: string
  filterOption?: (option: PremiumSelectOption, query: string) => boolean
  className?: string
  contentClassName?: string
  ariaLabel?: string
}

function cx(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(' ')
}

function ChevronDownIcon(props: { className?: string }) {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="none"
      aria-hidden="true"
      className={cx('h-4 w-4', props.className)}
    >
      <path
        d="M6 8l4 4 4-4"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function CheckIcon(props: { className?: string }) {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="none"
      aria-hidden="true"
      className={cx('h-4 w-4', props.className)}
    >
      <path
        d="M16 6l-7 8-3-3"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export function PremiumSelect({
  id,
  value,
  onValueChange,
  options,
  placeholder = 'Select…',
  disabled,
  searchable,
  searchMinOptions = 8,
  searchPlaceholder = 'Search…',
  searchAriaLabel = 'Search options',
  filterOption,
  className,
  contentClassName,
  ariaLabel,
}: Props) {
  const controlledValue = value ?? ''

  const selectedLabel = React.useMemo(() => {
    if (!controlledValue) return ''
    return options.find((o) => o.value === controlledValue)?.label ?? ''
  }, [options, controlledValue])

  // Radix Select forbids Select.Item values being an empty string.
  // Empty string is reserved for clearing the selection / showing the placeholder.
  const safeOptions = React.useMemo(() => options.filter((o) => o.value !== ''), [options])

  const isSearchable = searchable !== false
  const showSearchInput = isSearchable && options.length >= searchMinOptions
  const [open, setOpen] = React.useState(false)
  const [query, setQuery] = React.useState('')

  React.useEffect(() => {
    if (!open) setQuery('')
  }, [open])

  const normalizedQuery = query.trim().toLowerCase()
  const filteredOptions = React.useMemo(() => {
    if (!showSearchInput) return safeOptions
    if (!normalizedQuery) return safeOptions
    const fn =
      filterOption ??
      ((o: PremiumSelectOption, q: string) => String(o.label).toLowerCase().includes(q) || String(o.value).toLowerCase().includes(q))
    return safeOptions.filter((o) => fn(o, normalizedQuery))
  }, [filterOption, normalizedQuery, safeOptions, showSearchInput])

  return (
    <Select.Root
      value={controlledValue}
      onValueChange={onValueChange}
      disabled={disabled}
      open={open}
      onOpenChange={setOpen}
    >
      <Select.Trigger
        id={id}
        aria-label={ariaLabel}
        className={cx(
          // Uses select-premium base styles; select-premium-btn strips the CSS
          // background arrow since we render our own ChevronDownIcon in JSX.
          'select-premium select-premium-btn inline-flex w-full min-w-0 items-center justify-between gap-2 text-left',
          disabled && 'opacity-80',
          className,
        )}
      >
        <span className="min-w-0 flex-1 truncate" title={selectedLabel}>
          <Select.Value placeholder={placeholder} />
        </span>
        <Select.Icon className="text-emerald-600">
          <ChevronDownIcon />
        </Select.Icon>
      </Select.Trigger>

      <Select.Portal>
        <Select.Content
          position="popper"
          sideOffset={8}
          className={cx(
            'w-[var(--radix-popper-anchor-width)]',
            'z-[60] overflow-hidden rounded-2xl border border-emerald-200/70 bg-white/85 backdrop-blur-xl shadow-[0_18px_60px_rgba(15,23,42,0.18)]',
            'animate-fadeIn',
            contentClassName,
          )}
        >
          {showSearchInput ? (
            <div className="p-2">
              <input
                className="input-premium w-full text-sm"
                placeholder={searchPlaceholder}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label={searchAriaLabel}
                onKeyDown={(e) => {
                  // Prevent Select's typeahead from fighting the input
                  e.stopPropagation()
                }}
              />
            </div>
          ) : null}

          <Select.Viewport className={cx('max-h-[320px] p-1', showSearchInput && 'pt-0')}>
            {filteredOptions.length === 0 ? (
              <div className="px-3 py-2 text-sm text-slate-500">
                {showSearchInput && normalizedQuery ? 'No matches' : 'No options'}
              </div>
            ) : (
              filteredOptions.map((o) => (
                <Select.Item
                  key={o.value}
                  value={o.value}
                  disabled={o.disabled}
                  title={o.label}
                  className={cx(
                    'relative flex select-none items-center gap-2 rounded-xl px-3 py-2 text-sm outline-none',
                    'text-slate-800 data-[highlighted]:bg-emerald-50 data-[highlighted]:text-slate-900',
                    'data-[disabled]:opacity-45 data-[disabled]:cursor-not-allowed',
                  )}
                >
                  <Select.ItemIndicator className="absolute left-2 inline-flex items-center justify-center text-emerald-600">
                    <CheckIcon />
                  </Select.ItemIndicator>
                  <div className="min-w-0 pl-5">
                    <Select.ItemText>
                      <span className="block truncate">{o.label}</span>
                    </Select.ItemText>
                  </div>
                </Select.Item>
              ))
            )}
          </Select.Viewport>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
  )
}
