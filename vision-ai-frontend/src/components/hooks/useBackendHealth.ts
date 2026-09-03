import { useEffect, useState } from "react"
import axios from "axios"

type BackendStatus =
  | "connected"
  | "disconnected"
  | "checking"

const API_URL = "http://127.0.0.1:8000"

export function useBackendHealth() {
  const [status, setStatus] =
    useState<BackendStatus>("checking")

  useEffect(() => {
    let mounted = true

    const checkHealth = async () => {
      try {
        const res = await axios.get(`${API_URL}/`, {
          timeout: 3000,
        })

        console.log("Backend response:", res.data)

        if (res.data?.status === true) {
          mounted && setStatus("connected")
        } else {
          mounted && setStatus("disconnected")
        }
      } catch (err) {
        console.error("Backend failed:", err)

        mounted && setStatus("disconnected")
      }
    }

    checkHealth()

    const interval = setInterval(checkHealth, 2000)

    return () => {
      mounted = false
      clearInterval(interval)
    }
  }, [])

  return status
}