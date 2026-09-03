import { api } from "../../lib/api"

export const inferenceService = {
  async start() {
    return api.post("/oak/oak/inference/start")
  },

  async stop() {
    return api.post("/oak/oak/inference/stop")
  },
}