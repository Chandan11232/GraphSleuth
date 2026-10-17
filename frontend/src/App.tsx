import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Landing } from './pages/Landing'
import { Dashboard } from './pages/Dashboard'
import { NewInvestigation } from './pages/NewInvestigation'
import { InvestigationView } from './pages/InvestigationView'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route element={<Layout />}>
        <Route path="/repos" element={<Dashboard />} />
        <Route path="/investigate" element={<NewInvestigation />} />
        <Route path="/investigations/:investigationId" element={<InvestigationView />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
