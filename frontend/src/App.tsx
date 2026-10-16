import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Dashboard } from './pages/Dashboard'
import { NewInvestigation } from './pages/NewInvestigation'
import { InvestigationView } from './pages/InvestigationView'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/investigate" element={<NewInvestigation />} />
        <Route path="/investigations/:investigationId" element={<InvestigationView />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
