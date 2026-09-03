import { api } from "../../lib/api"

export const recordingService = {

  async startRecording(sessionId: string) {
    const res = await api.post("/recording/start", { session_id: sessionId })
    return res.data as { status: string; recording_path: string }
  },

  async stopRecording(sessionId: string) {
    const res = await api.post("/recording/stop", { session_id: sessionId })
    return res.data as { status: string }
  },

}
