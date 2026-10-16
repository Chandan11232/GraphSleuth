import { useCallback, useEffect, useState } from 'react'
import { api, errorMessage, type Repository, type RepositoryStats } from '../services/api'

export function useRepositories() {
  const [repositories, setRepositories] = useState<Repository[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchRepositories = useCallback(async () => {
    try {
      const response = await api.get('/repos')
      setRepositories(response.data.repositories)
      setError(null)
    } catch (err) {
      setError(errorMessage(err, 'Could not reach the backend'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchRepositories()
  }, [fetchRepositories])

  // Poll while any repository is still being ingested.
  const busy = repositories.some(r => r.status === 'pending' || r.status === 'ingesting')
  useEffect(() => {
    if (!busy) return
    const timer = setInterval(fetchRepositories, 1500)
    return () => clearInterval(timer)
  }, [busy, fetchRepositories])

  const createRepository = async (data: { name: string; url: string; branch?: string; github_token?: string }) => {
    const response = await api.post('/repos', data)
    setRepositories(prev => [response.data, ...prev])
    return response.data as Repository
  }

  const deleteRepository = async (id: string) => {
    await api.delete(`/repos/${id}`)
    setRepositories(prev => prev.filter(r => r.id !== id))
  }

  return { repositories, loading, error, refetch: fetchRepositories, createRepository, deleteRepository }
}

export function useRepositoryStats(repoId: string | null, ready: boolean) {
  const [stats, setStats] = useState<RepositoryStats | null>(null)
  useEffect(() => {
    if (!repoId || !ready) return
    api.get(`/repos/${repoId}/stats`).then(r => setStats(r.data)).catch(() => setStats(null))
  }, [repoId, ready])
  return stats
}
