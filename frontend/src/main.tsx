import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router'

import { AppShell } from '@/components/layout/app-shell'
import { AuthGate } from '@/auth'
import { ThemeProvider } from '@/components/theme-provider'
import { DashboardPage } from '@/pages/dashboard'
import { ExplorerPage } from '@/pages/explorer'
import { InspectorPage } from '@/pages/inspector'
import { HistoryPageView, JobPage } from '@/pages/jobs'
import { TransformationsRoute } from '@/pages/transformations'
import { NotFoundPage } from '@/pages/not-found'
import { SchemasPage } from '@/pages/schemas'
import { QualityPageView } from '@/pages/quality'
import { CollectionsPage, CollectionPage } from '@/pages/collections'
import { AnalyticsPage } from '@/pages/analytics'

import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A self-hosted backend on the LAN: retrying forever hides real outages.
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

const container = document.getElementById('root')
if (!container) {
  throw new Error('Root container #root is missing from index.html')
}

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <AuthGate>
          <BrowserRouter>
            <Routes>
            <Route element={<AppShell />}>
              <Route index element={<DashboardPage />} />
              <Route path="documents" element={<ExplorerPage />} />
              <Route path="documents/:documentId" element={<InspectorPage />} />
              <Route path="transformations" element={<TransformationsRoute />} />
              <Route path="jobs" element={<HistoryPageView />} />
              <Route path="history" element={<HistoryPageView />} />
              <Route path="jobs/:jobId" element={<JobPage />} />
              <Route path="schemas" element={<SchemasPage />} />
              <Route path="quality" element={<QualityPageView />} />
              <Route path="collections" element={<CollectionsPage />} />
              <Route path="collections/:collectionId" element={<CollectionPage />} />
              <Route path="analytics" element={<AnalyticsPage />} />
              <Route path="*" element={<NotFoundPage />} />
            </Route>
            </Routes>
          </BrowserRouter>
        </AuthGate>
      </ThemeProvider>
    </QueryClientProvider>
  </StrictMode>,
)
