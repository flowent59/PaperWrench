import { Link } from 'react-router'

import { buttonVariants } from '@/components/ui/button'
import { messages } from '@/i18n/messages'

export function NotFoundPage() {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-4 py-24 text-center">
      <h1 className="text-3xl font-semibold">404</h1>
      <p className="text-sm text-muted-foreground">
        {messages.placeholder.milestone}
      </p>
      <Link to="/" className={buttonVariants({ variant: 'outline' })}>
        {messages.nav.dashboard}
      </Link>
    </div>
  )
}
