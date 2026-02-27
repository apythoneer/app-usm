import axios from 'axios'

export const apiClient = axios.create({
  baseURL: '/api/v1',
  headers: { 'Content-Type': 'application/json' },
  timeout: 30_000,
})

// Intercept 401 — placeholder for Phase 3 auth
apiClient.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      // TODO Phase 3: redirect to login
      console.warn('Unauthorized — auth not yet enforced')
    }
    return Promise.reject(err)
  }
)
